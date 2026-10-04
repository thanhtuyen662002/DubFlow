from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.translation import (
    DeterministicFixtureBackend,
    LocalTranslationAdapter,
    RoutedTranslationAdapter,
    SourceSegment,
    TranslationBackendError,
    TranslationBackendResult,
    TranslationCandidate,
    TranslationCheckpoint,
    TranslationConfig,
    TranslationError,
    TranslationProvenance,
    TranslationRequest,
    TranslationStageError,
    parse_translation_json,
    resolve_source_language,
    validate_translation_document,
)


BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "1" * 64
MODEL_HASH = "sha256:" + "2" * 64
EMPTY_GLOSSARY_HASH = "sha256:" + hashlib.sha256(b"{}").hexdigest()


def point(ticks: int) -> TimePoint:
    return TimePoint(ticks, BASE)


def source(identifier: str, text: str, start: int, end: int, language: str = "en") -> SourceSegment:
    return SourceSegment(identifier, text, point(start), point(end), language, 0.9)


def make_provenance(config: TranslationConfig, *, hardware: str = "fixture", fallback: str | None = None) -> TranslationProvenance:
    return TranslationProvenance(
        "dubflow-translation-test",
        "1.0.0",
        "fixture-v1",
        "fixture-model",
        "1",
        "python-stdlib",
        "timeline-v1",
        config.to_hash(),
        MODEL_HASH,
        EMPTY_GLOSSARY_HASH,
        INPUT_HASH,
        config.requested_profile,
        hardware,
        fallback,
    )


def adapter(backend, *, config: TranslationConfig | None = None, fallback=None) -> LocalTranslationAdapter:
    config = config or TranslationConfig(max_items_per_chunk=2, context_before=1, context_after=1, requested_profile="fixture")
    return LocalTranslationAdapter(backend, config=config, provenance=make_provenance(config), fallback_backend=fallback)


class TranslationAdapterTests(unittest.TestCase):
    def test_fixture_preserves_stable_identity_timing_names_numbers_and_punctuation(self) -> None:
        item = source("u-1", "Hello, Nguyen Van A 2.0!", 100, 900)
        backend = DeterministicFixtureBackend({"u-1": "Xin chào, Nguyễn Văn A 2.0!"})
        result = adapter(backend).translate((item,), input_hash=INPUT_HASH)
        translated = result.translations[0]
        self.assertEqual(translated.source_utterance_id, "u-1")
        self.assertEqual(translated.translated_text, "Xin chào, Nguyễn Văn A 2.0!")
        self.assertEqual(translated.start, item.start)
        self.assertEqual(translated.end, item.end)
        self.assertEqual(result.provenance.glossary_hash, EMPTY_GLOSSARY_HASH)
        validate_translation_document(result.to_dict())
        self.assertEqual(parse_translation_json(result.to_json())["target_language"], "vi")

    def test_context_window_contains_bounded_before_and_after_segments(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, context_before=1, context_after=1, requested_profile="fixture")
        items = tuple(source(f"u-{index}", f"line {index}", index * 100, index * 100 + 50) for index in range(3))
        backend = DeterministicFixtureBackend({item.utterance_id: f"vi {item.utterance_id}" for item in items})
        adapter(backend, config=config).translate(items, input_hash=INPUT_HASH)
        self.assertEqual(backend.contexts[0], ("u-0", "u-1"))
        self.assertEqual(backend.contexts[1], ("u-0", "u-1", "u-2"))
        self.assertEqual(backend.contexts[2], ("u-1", "u-2"))

    def test_planner_sorts_timeline_without_changing_source_ids(self) -> None:
        config = TranslationConfig(max_items_per_chunk=2, requested_profile="fixture")
        backend = DeterministicFixtureBackend({"early": "sớm", "late": "muộn"})
        result = adapter(backend, config=config).translate(
            (source("late", "late", 500, 600), source("early", "early", 100, 200)),
            input_hash=INPUT_HASH,
        )
        self.assertEqual([item.source_utterance_id for item in result.translations], ["early", "late"])
        self.assertEqual(result.source_utterance_ids, ("early", "late"))

    def test_checkpoint_reuse_skips_only_matching_chunk(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        running_backend = DeterministicFixtureBackend({"u-1": "một", "u-2": "hai"})
        running = adapter(running_backend, config=config)
        items = (source("u-1", "one", 0, 100), source("u-2", "two", 200, 300))
        first = running.translate(items, input_hash=INPUT_HASH)
        plan = __import__("engine.dubflow.translation", fromlist=["TranslationPlanner"]).TranslationPlanner(config).plan(items, input_hash=INPUT_HASH)[0]
        checkpoint_result = TranslationBackendResult((TranslationCandidate("u-1", "một"),))
        checkpoint = TranslationCheckpoint(
            plan.chunk_id,
            running._artifact_hash(plan, INPUT_HASH, (), checkpoint_result),
            checkpoint_result,
        )
        second_backend = DeterministicFixtureBackend({"u-1": "wrong", "u-2": "hai"})
        second = adapter(second_backend, config=config).translate(
            items, input_hash=INPUT_HASH, checkpoints={plan.chunk_id: checkpoint}
        )
        self.assertEqual(second.chunks[0]["status"], "skipped")
        self.assertNotIn(plan.chunk_id, second_backend.calls)
        self.assertEqual(second.translations[0].translated_text, "một")
        self.assertEqual(len(second_backend.calls), 1)
        self.assertEqual(first.source_utterance_ids, second.source_utterance_ids)

    def test_fallback_failure_evidence_and_mixed_provenance_are_explicit(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        items = (source("u-1", "one", 0, 100), source("u-2", "two", 200, 300))
        planner = __import__("engine.dubflow.translation", fromlist=["TranslationPlanner"]).TranslationPlanner(config)
        plans = planner.plan(items, input_hash=INPUT_HASH)
        primary = DeterministicFixtureBackend(
            {"u-2": "hai"},
            failures={plan.chunk_id: TranslationBackendError("MODEL_UNAVAILABLE", "model missing", retryable=False) for plan in plans[:1]},
        )
        fallback = DeterministicFixtureBackend({"u-1": "một", "u-2": "hai"})
        result = adapter(primary, config=config, fallback=fallback).translate(items, input_hash=INPUT_HASH)
        self.assertEqual(result.provenance.hardware_profile, "mixed")
        self.assertTrue(any(item.fallback_used for item in result.failures))
        self.assertEqual([item.translated_text for item in result.translations], ["một", "hai"])

    def test_malformed_primary_result_uses_validated_fallback(self) -> None:
        item = source("u-1", "hello", 0, 100)

        class MalformedBackend:
            def translate(self, request: TranslationRequest) -> TranslationBackendResult:
                return TranslationBackendResult(())

        result = adapter(MalformedBackend(), fallback=DeterministicFixtureBackend({"u-1": "xin chào"})).translate(
            (item,), input_hash=INPUT_HASH
        )
        self.assertEqual(result.translations[0].translated_text, "xin chào")
        self.assertTrue(any(item.code == "MISSING_TRANSLATION" for item in result.failures))
        self.assertTrue(all(item.fallback_used for item in result.failures))

    def test_retry_requires_changed_condition(self) -> None:
        item = source("u-1", "hello", 0, 100)
        config = TranslationConfig(max_attempts=2, requested_profile="fixture")

        class RetryBackend:
            def __init__(self) -> None:
                self.calls = 0

            def translate(self, request: TranslationRequest) -> TranslationBackendResult:
                self.calls += 1
                if self.calls == 1:
                    raise TranslationBackendError("TEMPORARY", "reduce batch", retryable=True)
                return TranslationBackendResult((TranslationCandidate("u-1", "xin chào"),))

        retry = RetryBackend()
        result = adapter(retry, config=config).translate((item,), input_hash=INPUT_HASH)
        self.assertEqual(retry.calls, 2)
        self.assertEqual(result.failures[0].attempt, 1)

        class StuckBackend:
            def translate(self, request: TranslationRequest) -> TranslationBackendResult:
                raise TranslationBackendError("TEMPORARY", "same condition", retryable=True)

        with self.assertRaises(TranslationStageError) as context:
            adapter(StuckBackend(), config=config).translate((item,), input_hash=INPUT_HASH)
        self.assertTrue(any(item.code == "RETRY_CONDITION_UNCHANGED" for item in context.exception.document.failures))

    def test_provenance_rejects_wrong_config_or_glossary_hash(self) -> None:
        config = TranslationConfig(requested_profile="fixture")
        with self.assertRaisesRegex(ValueError, "PROVENANCE_CONFIG_MISMATCH"):
            LocalTranslationAdapter(
                DeterministicFixtureBackend({}),
                config=config,
                provenance=make_provenance(TranslationConfig(max_items_per_chunk=2, requested_profile="fixture")),
            )
        glossary = {"hello": "xin chào"}
        with self.assertRaisesRegex(ValueError, "PROVENANCE_GLOSSARY_MISMATCH"):
            adapter(DeterministicFixtureBackend({})).translate((source("u-1", "hello", 0, 100),), input_hash=INPUT_HASH, glossary=glossary)

    def test_wire_rejects_duplicate_fields_and_noncanonical_ticks(self) -> None:
        result = adapter(DeterministicFixtureBackend({"u-1": "xin"})).translate(
            (source("u-1", "hi", 0, 100),), input_hash=INPUT_HASH
        )
        invalid = result.to_dict()
        invalid["translations"][0]["start"]["ticks"] = "-0"
        with self.assertRaisesRegex(ValueError, "canonical decimal"):
            validate_translation_document(invalid)
        duplicate = result.to_json().replace('"kind":"translation_document"', '"kind":"translation_document","kind":"translation_document"', 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
            parse_translation_json(duplicate)

    def test_source_language_resolution_is_explicit_and_fail_closed(self) -> None:
        self.assertEqual(
            resolve_source_language((source("u-1", "你好", 0, 100, language="zh-cn"),)),
            "zh-CN",
        )
        self.assertEqual(
            resolve_source_language(
                (source("u-1", "?", 0, 100, language="und"),),
                requested_source_language="zh-tw",
            ),
            "zh-TW",
        )
        self.assertEqual(
            resolve_source_language(
                (source("u-1", "你好", 0, 100, language="zh"),),
                requested_source_language="zh-CN",
            ),
            "zh-CN",
        )
        self.assertEqual(
            resolve_source_language(
                (source("u-1", "你好", 0, 100, language="zh"),),
                requested_source_language="zh-TW",
            ),
            "zh-TW",
        )
        with self.assertRaisesRegex(TranslationError, "SOURCE_LANGUAGE_MISMATCH"):
            resolve_source_language(
                (source("u-1", "你好", 0, 100, language="zh-TW"),),
                requested_source_language="zh-CN",
            )
        with self.assertRaisesRegex(TranslationError, "SOURCE_LANGUAGE_UNRESOLVED"):
            resolve_source_language((source("u-1", "?", 0, 100, language="und"),))
        with self.assertRaisesRegex(TranslationError, "SOURCE_LANGUAGE_AMBIGUOUS"):
            resolve_source_language((
                source("u-1", "hello", 0, 100, language="en"),
                source("u-2", "你好", 200, 300, language="zh-CN"),
            ))

    def test_routed_translation_keeps_chinese_variants_distinct_and_provenance_truthful(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")

        def route(source_language: str, backend_id: str, translated: str, model_digit: str):
            backend = DeterministicFixtureBackend({"u-1": translated})
            provenance = TranslationProvenance(
                "dubflow-translation-test",
                "1.0.0",
                backend_id,
                f"{backend_id}-model",
                "1",
                "python-stdlib",
                "timeline-v1",
                config.to_hash(),
                "sha256:" + model_digit * 64,
                EMPTY_GLOSSARY_HASH,
                INPUT_HASH,
                config.requested_profile,
                "fixture",
            )
            return backend, LocalTranslationAdapter(
                backend,
                config=config,
                provenance=provenance,
                supported_source_language=source_language,
            )

        zh_backend, zh_route = route("zh", "fixture-zh-vi", "zh", "3")
        cn_backend, cn_route = route("zh-CN", "fixture-zh-cn-vi", "giản thể", "4")
        tw_backend, tw_route = route("zh-TW", "fixture-zh-tw-vi", "phồn thể", "5")
        router = RoutedTranslationAdapter({"zh": zh_route, "zh-CN": cn_route, "zh-TW": tw_route})

        original = source("u-1", "你好", 123, 456, language="zh-CN")
        result = router.translate((original,), input_hash=INPUT_HASH)

        self.assertEqual(result.source_language, "zh-CN")
        self.assertEqual(result.target_language, "vi")
        self.assertEqual(result.provenance.backend_id, "fixture-zh-cn-vi")
        self.assertEqual(result.provenance.model_id, "fixture-zh-cn-vi-model")
        self.assertEqual(result.provenance.model_hash, "sha256:" + "4" * 64)
        self.assertEqual(result.translations[0].source_utterance_id, original.utterance_id)
        self.assertEqual(result.translations[0].start, original.start)
        self.assertEqual(result.translations[0].end, original.end)
        self.assertEqual(result.translations[0].translated_text, "giản thể")
        self.assertEqual(len(cn_backend.calls), 1)
        self.assertEqual(zh_backend.calls, [])
        self.assertEqual(tw_backend.calls, [])

    def test_routed_translation_canonicalizes_backend_boundary_and_requires_capability_identity(self) -> None:
        config = TranslationConfig(
            max_items_per_chunk=1,
            context_before=1,
            context_after=1,
            requested_profile="fixture",
        )

        class CapturingBackend:
            def __init__(self) -> None:
                self.languages: list[tuple[str, ...]] = []

            def translate(self, request: TranslationRequest) -> TranslationBackendResult:
                self.languages.append(tuple(item.source_language for item in request.all_segments))
                return TranslationBackendResult(tuple(
                    TranslationCandidate(item.utterance_id, f"vi {item.utterance_id}")
                    for item in request.core
                ))

        backend = CapturingBackend()
        zh_cn = LocalTranslationAdapter(
            backend,
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-cn",
        )
        router = RoutedTranslationAdapter({"zh-CN": zh_cn})
        result = router.translate((
            source("u-1", "一", 0, 50, language="zh-cn"),
            source("u-2", "二", 100, 150, language="ZH-CN"),
            source("u-3", "三", 200, 250, language="zh-CN"),
        ), input_hash=INPUT_HASH)

        self.assertEqual(result.source_language, "zh-CN")
        self.assertTrue(all(item.source_language == "zh-CN" for item in result.translations))
        self.assertEqual(backend.languages, [
            ("zh-CN", "zh-CN"),
            ("zh-CN", "zh-CN", "zh-CN"),
            ("zh-CN", "zh-CN"),
        ])

        missing_capability = LocalTranslationAdapter(
            DeterministicFixtureBackend({"u-1": "x"}),
            config=config,
            provenance=make_provenance(config),
        )
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_CAPABILITY_MISSING"):
            RoutedTranslationAdapter({"zh-CN": missing_capability})

        mismatched_capability = LocalTranslationAdapter(
            DeterministicFixtureBackend({"u-1": "x"}),
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-TW",
        )
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_CAPABILITY_MISMATCH"):
            RoutedTranslationAdapter({"zh-CN": mismatched_capability})

        for invalid_capability in ("auto", "und"):
            with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_CAPABILITY_INVALID"):
                LocalTranslationAdapter(
                    DeterministicFixtureBackend({"u-1": "x"}),
                    config=config,
                    provenance=make_provenance(config),
                    supported_source_language=invalid_capability,
                )

    def test_declared_local_route_rejects_direct_source_capability_bypass(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        backend = DeterministicFixtureBackend({"u-1": "wrong route", "u-2": "đúng"})
        route = LocalTranslationAdapter(
            backend,
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-CN",
        )

        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_SOURCE_MISMATCH"):
            route.translate(
                (source("u-1", "hello", 0, 100, language="en"),),
                input_hash=INPUT_HASH,
            )
        self.assertEqual(backend.calls, [])

        result = route.translate(
            (source("u-2", "你好", 0, 100, language="ZH-CN"),),
            input_hash=INPUT_HASH,
        )
        self.assertEqual(result.source_language, "zh-CN")
        self.assertEqual(result.translations[0].source_language, "zh-CN")
        self.assertEqual(result.translations[0].translated_text, "đúng")

    def test_routed_translation_rejects_noncanonical_returned_source_provenance(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")

        class AliasDocumentRoute(LocalTranslationAdapter):
            def translate(self, sources, **kwargs):
                document = super().translate(sources, **kwargs)
                return replace(document, source_language="zh-cn")

        route = AliasDocumentRoute(
            DeterministicFixtureBackend({"u-1": "xin chào"}),
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-CN",
        )
        router = RoutedTranslationAdapter({"zh-CN": route})
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_PROVENANCE_MISMATCH"):
            router.translate(
                (source("u-1", "你好", 0, 100, language="zh-CN"),),
                input_hash=INPUT_HASH,
            )

    def test_routed_checkpoint_cannot_cross_source_route_identity(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")

        def route(source_language: str, backend_id: str, translated: str):
            backend = DeterministicFixtureBackend({"u-1": translated})
            provenance = replace(
                make_provenance(config),
                backend_id=backend_id,
                model_id=f"{backend_id}-model",
            )
            return backend, LocalTranslationAdapter(
                backend,
                config=config,
                provenance=provenance,
                supported_source_language=source_language,
            )

        cn_backend, cn_route = route("zh-CN", "fixture-zh-cn-vi", "giản thể")
        tw_backend, tw_route = route("zh-TW", "fixture-zh-tw-vi", "phồn thể")
        original = source("u-1", "你好", 0, 100, language="und")
        planner = __import__(
            "engine.dubflow.translation",
            fromlist=["TranslationPlanner"],
        ).TranslationPlanner(config)
        cn_plan = planner.plan(
            (replace(original, source_language="zh-CN"),),
            input_hash=INPUT_HASH,
        )[0]
        tw_plan = planner.plan(
            (replace(original, source_language="zh-TW"),),
            input_hash=INPUT_HASH,
        )[0]
        self.assertEqual(cn_plan.chunk_id, tw_plan.chunk_id)

        stale_result = TranslationBackendResult(
            (TranslationCandidate("u-1", "giản thể"),)
        )
        stale_checkpoint = TranslationCheckpoint(
            cn_plan.chunk_id,
            cn_route._artifact_hash(cn_plan, INPUT_HASH, (), stale_result),
            stale_result,
        )

        router = RoutedTranslationAdapter({"zh-TW": tw_route})
        result = router.translate(
            (original,),
            input_hash=INPUT_HASH,
            source_language="zh-TW",
            checkpoints={tw_plan.chunk_id: stale_checkpoint},
        )

        self.assertEqual(result.source_language, "zh-TW")
        self.assertEqual(result.translations[0].translated_text, "phồn thể")
        self.assertEqual(result.chunks[0]["status"], "completed")
        self.assertEqual(len(tw_backend.calls), 1)
        self.assertEqual(cn_backend.calls, [])

    def test_routed_translation_rejects_unfenced_fallback_backend(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        fallback = DeterministicFixtureBackend({"u-1": "wrong fallback"})
        route = LocalTranslationAdapter(
            DeterministicFixtureBackend({"u-1": "primary"}),
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-CN",
            fallback_backend=fallback,
        )

        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_FALLBACK_UNSAFE"):
            RoutedTranslationAdapter({"zh-CN": route})
        self.assertEqual(fallback.calls, [])

    def test_routed_translation_rejects_swapped_backend_model_provenance(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")

        class SwappedProvenanceRoute(LocalTranslationAdapter):
            def translate(self, sources, **kwargs):
                document = super().translate(sources, **kwargs)
                return replace(
                    document,
                    provenance=replace(document.provenance, backend_id="unexpected-backend"),
                )

        route = SwappedProvenanceRoute(
            DeterministicFixtureBackend({"u-1": "xin chào"}),
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-CN",
        )
        router = RoutedTranslationAdapter({"zh-CN": route})

        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_PROVENANCE_MISMATCH"):
            router.translate(
                (source("u-1", "你好", 0, 100, language="zh-CN"),),
                input_hash=INPUT_HASH,
            )

    def test_routed_translation_rejects_segment_language_provenance_drift(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")

        class SegmentAliasRoute(LocalTranslationAdapter):
            def translate(self, sources, **kwargs):
                document = super().translate(sources, **kwargs)
                aliased = tuple(
                    replace(item, source_language="zh-cn")
                    for item in document.translations
                )
                return replace(document, translations=aliased)

        route = SegmentAliasRoute(
            DeterministicFixtureBackend({"u-1": "xin chào"}),
            config=config,
            provenance=make_provenance(config),
            supported_source_language="zh-CN",
        )
        router = RoutedTranslationAdapter({"zh-CN": route})

        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_PROVENANCE_MISMATCH"):
            router.translate(
                (source("u-1", "你好", 0, 100, language="zh-CN"),),
                input_hash=INPUT_HASH,
            )

    def test_routed_translation_preserves_existing_english_route(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        backend = DeterministicFixtureBackend({"u-1": "xin chào"})
        router = RoutedTranslationAdapter({
            "en": LocalTranslationAdapter(
                backend,
                config=config,
                provenance=make_provenance(config),
                supported_source_language="en",
            )
        })
        result = router.translate((source("u-1", "hello", 0, 100, language="en"),), input_hash=INPUT_HASH)
        self.assertEqual(result.source_language, "en")
        self.assertEqual(result.translations[0].translated_text, "xin chào")
        self.assertEqual(len(backend.calls), 1)

    def test_routed_translation_never_falls_back_to_english_for_unknown_or_missing_chinese_route(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        english_backend = DeterministicFixtureBackend({"u-1": "wrong route"})
        router = RoutedTranslationAdapter({
            "en": LocalTranslationAdapter(
                english_backend,
                config=config,
                provenance=make_provenance(config),
                supported_source_language="en",
            )
        })
        with self.assertRaisesRegex(TranslationError, "SOURCE_LANGUAGE_UNRESOLVED"):
            router.translate((source("u-1", "?", 0, 100, language="und"),), input_hash=INPUT_HASH)
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_UNAVAILABLE"):
            router.translate((source("u-1", "你好", 0, 100, language="zh-CN"),), input_hash=INPUT_HASH)
        self.assertEqual(english_backend.calls, [])

    def test_explicit_chinese_source_can_resolve_undetermined_input_without_changing_timeline(self) -> None:
        config = TranslationConfig(max_items_per_chunk=1, requested_profile="fixture")
        backend = DeterministicFixtureBackend({"u-1": "xin chào"})
        provenance = TranslationProvenance(
            "dubflow-translation-test", "1.0.0", "fixture-zh-tw-vi",
            "fixture-zh-tw-vi-model", "1", "python-stdlib", "timeline-v1",
            config.to_hash(), "sha256:" + "6" * 64, EMPTY_GLOSSARY_HASH,
            INPUT_HASH, config.requested_profile, "fixture",
        )
        router = RoutedTranslationAdapter({
            "zh-TW": LocalTranslationAdapter(
                backend,
                config=config,
                provenance=provenance,
                supported_source_language="zh-TW",
            )
        })
        original = source("u-1", "你好", 321, 654, language="und")
        result = router.translate((original,), input_hash=INPUT_HASH, source_language="zh-tw")
        translated = result.translations[0]
        self.assertEqual(result.source_language, "zh-TW")
        self.assertEqual(translated.source_utterance_id, original.utterance_id)
        self.assertEqual(translated.start, original.start)
        self.assertEqual(translated.end, original.end)

    def test_schema_document_matches_wire_surface(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "contracts" / "translation" / "schema-v1.json").read_text(encoding="utf-8"))
        result = adapter(DeterministicFixtureBackend({"u-1": "xin"})).translate(
            (source("u-1", "hi", 0, 100),), input_hash=INPUT_HASH
        )
        self.assertEqual(set(result.to_dict()), set(schema["required"]))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/translation/schema-v1.json")


if __name__ == "__main__":
    unittest.main()

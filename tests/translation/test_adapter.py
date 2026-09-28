from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.translation import (
    DeterministicFixtureBackend,
    LocalTranslationAdapter,
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

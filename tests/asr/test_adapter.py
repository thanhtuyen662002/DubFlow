from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.asr import (
    AdapterConfig,
    AsrBackendError,
    AsrStageError,
    BackendResult,
    ChunkCheckpoint,
    ChunkPlanner,
    DeterministicFixtureBackend,
    LocalAsrAdapter,
    Provenance,
    RawUtterance,
    RawWord,
    TimeBase,
    TimeInterval,
    TimePoint,
    VadSegment,
    map_sample_interval,
    parse_transcript_json,
    validate_transcript,
)


BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "1" * 64
CONFIG_HASH = AdapterConfig(2000, 500, requested_profile="fixture").to_hash()


def point(ticks: int) -> TimePoint:
    return TimePoint(ticks, BASE)


def word(word_id: str, text: str, start: int, end: int, confidence: float = 0.9) -> RawWord:
    return RawWord(word_id, text, point(start), point(end), confidence)


def utterance(
    utterance_id: str,
    text: str,
    start: int,
    end: int,
    words: tuple[RawWord, ...],
    confidence: float = 0.9,
) -> RawUtterance:
    return RawUtterance(utterance_id, point(start), point(end), "en", text, words, confidence)


def provenance(*, hardware: str = "fixture", requested: str = "fixture", fallback: str | None = None) -> Provenance:
    return Provenance(
        producer="dubflow-asr-test",
        producer_version="1.0.0",
        backend_id="fixture-cpu-v1",
        model_id="fixture-model",
        model_version="1",
        runtime="python-stdlib",
        timeline_contract="timeline-v1",
        config_hash=CONFIG_HASH,
        input_hash=INPUT_HASH,
        requested_profile=requested,
        hardware_profile=hardware,
        fallback_reason=fallback,
    )


def adapter(backend: DeterministicFixtureBackend, *, fallback=None) -> LocalAsrAdapter:
    return LocalAsrAdapter(
        backend,
        config=AdapterConfig(chunk_ticks=2000, overlap_ticks=500, requested_profile="fixture"),
        provenance=provenance(),
        fallback_backend=fallback,
    )


class AsrAdapterTests(unittest.TestCase):
    def test_sample_ticks_map_with_containing_floor_and_ceil_and_negative_anchor(self) -> None:
        interval = map_sample_interval(point(-100), 0, 1, 1000)
        self.assertEqual(interval.start.ticks, -100)
        self.assertEqual(interval.end.ticks, -99)

        mapped = map_sample_interval(point(0), 1, 2, 1_000_000_000)
        self.assertLess(mapped.start.ticks, mapped.end.ticks)

    def test_chunk_planner_has_non_overlapping_cores_and_stable_ids(self) -> None:
        duration = TimeInterval(point(0), point(4500))
        planner = ChunkPlanner(AdapterConfig(2000, 500, requested_profile="fixture"))
        first = planner.plan(duration, input_hash=INPUT_HASH)
        second = planner.plan(duration, input_hash=INPUT_HASH)
        self.assertEqual([item.chunk_id for item in first], [item.chunk_id for item in second])
        self.assertEqual([(item.core.start.ticks, item.core.end.ticks) for item in first], [(0, 2000), (2000, 4000), (4000, 4500)])
        self.assertEqual([(item.window.start.ticks, item.window.end.ticks) for item in first], [(0, 2500), (1500, 4500), (3500, 4500)])

    def test_overlap_merge_is_idempotent_and_preserves_same_text_separate_turns(self) -> None:
        shared = utterance(
            "source-span-1",
            " hello   world ",
            1800,
            2200,
            (word("w1", "hello", 1800, 2000), word("w2", "world", 2000, 2200)),
        )
        separate = utterance(
            "source-span-2",
            "hello world",
            2600,
            3000,
            (word("w3", "hello", 2600, 2800), word("w4", "world", 2800, 3000)),
        )
        simultaneous = utterance(
            "source-span-3",
            "yes",
            1600,
            1850,
            (word("w5", "yes", 1600, 1850),),
        )
        backend = DeterministicFixtureBackend(
            (shared, separate, simultaneous),
            vad_segments=(VadSegment(point(1800), point(2200), 0.8), VadSegment(point(1800), point(2200), 0.7)),
        )
        result = adapter(backend).transcribe(TimeInterval(point(0), point(4500)), input_hash=INPUT_HASH)
        self.assertEqual([item.utterance_id for item in result.utterances], ["source-span-3", "source-span-1", "source-span-2"])
        shared_result = next(item for item in result.utterances if item.utterance_id == "source-span-1")
        self.assertEqual(shared_result.text, "hello world")
        self.assertEqual(len(shared_result.words), 2)
        self.assertEqual(len(shared_result.chunk_ids), 2)
        self.assertEqual(len(result.vad_segments), 1)
        self.assertEqual(result.utterances[0].chunk_ids, tuple(sorted(result.utterances[0].chunk_ids)))
        encoded = json.loads(result.to_json())
        self.assertEqual(encoded["source_kind"], "asr")
        encoded_shared = next(item for item in encoded["utterances"] if item["utterance_id"] == "source-span-1")
        self.assertEqual(encoded_shared["raw_text"], " hello   world ")
        self.assertEqual(encoded_shared["start"]["ticks"], "1800")
        self.assertEqual(result.to_bytes(), result.to_bytes())

    def test_same_chunk_same_text_candidates_remain_distinct(self) -> None:
        first = utterance("same-a", "yes", 200, 500, (word("a", "yes", 200, 500),))
        second = utterance("same-b", "yes", 300, 600, (word("b", "yes", 300, 600),))
        result = adapter(DeterministicFixtureBackend((first, second))).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        self.assertEqual([item.utterance_id for item in result.utterances], ["same-a", "same-b"])

    def test_boundary_jitter_deduplicates_overlapping_words(self) -> None:
        first = utterance(
            "jitter-a",
            "hello world",
            1800,
            2200,
            (word("a1", "hello", 1800, 2000), word("a2", "world", 2000, 2200)),
        )
        jittered = utterance(
            "jitter-b",
            "hello world",
            1799,
            2199,
            (word("b1", "hello", 1799, 1999), word("b2", "world", 1999, 2199)),
        )

        class PerChunkBackend:
            def transcribe(self, chunk):
                if chunk.index == 0:
                    return BackendResult((first,), ())
                if chunk.index == 1:
                    return BackendResult((jittered,), ())
                return BackendResult((), ())

        result = adapter(PerChunkBackend()).transcribe(TimeInterval(point(0), point(4500)), input_hash=INPUT_HASH)
        self.assertEqual(len(result.utterances), 1)
        self.assertEqual(len(result.utterances[0].words), 2)

    def test_retry_budget_requires_changed_condition_and_preserves_failure_evidence(self) -> None:
        config = AdapterConfig(2000, 500, requested_profile="fixture", max_attempts=3)
        provenance_value = Provenance(
            "dubflow-asr-test", "1.0.0", "retry", "fixture", "1", "python-stdlib", "timeline-v1",
            config.to_hash(), INPUT_HASH, "fixture", "fixture",
        )
        source = utterance("retried", "ok", 100, 300, (word("rw", "ok", 100, 300),))

        class RetryBackend:
            def __init__(self):
                self.calls = 0

            def transcribe(self, chunk):
                self.calls += 1
                if self.calls == 1:
                    raise AsrBackendError("GPU_OOM", "reduce batch", retryable=True)
                return BackendResult((source,), ())

        retry_backend = RetryBackend()
        result = LocalAsrAdapter(retry_backend, config=config, provenance=provenance_value).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        self.assertEqual(retry_backend.calls, 2)
        self.assertEqual(result.failures[0].attempt, 1)
        self.assertEqual(result.failures[0].code, "GPU_OOM")

        class StuckBackend:
            def __init__(self):
                self.calls = 0

            def transcribe(self, chunk):
                self.calls += 1
                raise AsrBackendError("GPU_OOM", "same condition", retryable=True)

        stuck = StuckBackend()
        with self.assertRaises(AsrStageError) as context:
            LocalAsrAdapter(stuck, config=config, provenance=provenance_value).transcribe(
                TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
            )
        self.assertEqual(stuck.calls, 2)
        self.assertTrue(any(item.code == "RETRY_CONDITION_UNCHANGED" for item in context.exception.transcript.failures))

    def test_wire_validator_rejects_duplicate_and_noncanonical_wide_fields(self) -> None:
        source = utterance("wire", "hello", 100, 300, (word("ww", "hello", 100, 300),))
        result = adapter(DeterministicFixtureBackend((source,))).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        validate_transcript(result.to_dict())
        self.assertEqual(parse_transcript_json(result.to_json())["schema_version"], 1)
        invalid = result.to_dict()
        invalid["utterances"][0]["start"]["ticks"] = "-0"
        with self.assertRaisesRegex(ValueError, "canonical decimal"):
            validate_transcript(invalid)
        duplicate_json = result.to_json().replace('"kind":"asr_transcript"', '"kind":"asr_transcript","kind":"asr_transcript"', 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
            parse_transcript_json(duplicate_json)

    def test_schema_document_and_wire_output_share_required_v1_surface(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "contracts" / "asr" / "schema-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/asr/schema-v1.json")
        self.assertIn("suppressed_candidates", schema["required"])
        self.assertIn("vad_segments", schema["required"])
        source = utterance("schema", "hello", 100, 300, (word("sw", "hello", 100, 300),))
        result = adapter(DeterministicFixtureBackend((source,))).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        self.assertEqual(set(result.to_dict()), set(schema["required"]))
        validate_transcript(result.to_dict())

    def test_tiny_local_fixture_is_offline_and_preserves_nonzero_pts(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "tiny.json"
        backend = DeterministicFixtureBackend.from_json(fixture_path)
        result = adapter(backend).transcribe(TimeInterval(point(0), point(2000)), input_hash=INPUT_HASH)
        self.assertEqual(result.utterances[0].start.ticks, 900)
        self.assertEqual(result.utterances[0].end.ticks, 1540)
        self.assertEqual(result.provenance.hardware_profile, "fixture")

    def test_mixed_language_transcript_uses_und_without_losing_utterance_language(self) -> None:
        english = utterance("en", "hello", 100, 300, (word("en-w", "hello", 100, 300),))
        chinese = RawUtterance(
            "zh", point(400), point(600), "zh", "你好", (RawWord("zh-w", "你好", point(400), point(600), 0.8),), 0.8
        )
        result = adapter(DeterministicFixtureBackend((english, chinese))).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        self.assertEqual(result.language, "und")
        self.assertEqual({item.language for item in result.utterances}, {"en", "zh"})

    def test_hallucination_candidate_is_retained_as_suppressed_evidence(self) -> None:
        repeated_words = tuple(word(f"r{i}", "echo", 100 + i * 10, 110 + i * 10) for i in range(7))
        hallucination = utterance("hallucinated", "echo " * 7, 100, 180, repeated_words)
        result = adapter(DeterministicFixtureBackend((hallucination,))).transcribe(
            TimeInterval(point(0), point(1000)), input_hash=INPUT_HASH
        )
        self.assertEqual(result.utterances, ())
        self.assertEqual(result.suppressed_candidates[0].candidate_id, "hallucinated")
        self.assertIn("hallucination guard", result.suppressed_candidates[0].reason)

    def test_failed_chunk_degrades_with_fallback_and_all_failed_is_typed(self) -> None:
        duration = TimeInterval(point(0), point(4500))
        planner = ChunkPlanner(AdapterConfig(2000, 500, requested_profile="fixture"))
        chunks = planner.plan(duration, input_hash=INPUT_HASH)
        failure = AsrBackendError("MODEL_LOAD_FAILED", "fixture model unavailable", retryable=True)
        primary = DeterministicFixtureBackend((), failures={chunk.chunk_id: failure for chunk in chunks[:1]})
        fallback_utterance = utterance("fallback", "ok", 100, 300, (word("fw", "ok", 100, 300),))
        fallback = DeterministicFixtureBackend((fallback_utterance,))
        result = adapter(primary, fallback=fallback).transcribe(duration, input_hash=INPUT_HASH)
        self.assertTrue(result.failures[0].fallback_used)
        self.assertEqual(result.provenance.hardware_profile, "cpu")
        self.assertIn("fallback", result.provenance.fallback_reason or "")
        self.assertTrue(any(item.utterance_id == "fallback" for item in result.utterances))
        self.assertIn("degraded", " ".join(result.warnings))

        all_failed = DeterministicFixtureBackend((), failures={chunk.chunk_id: failure for chunk in chunks})
        with self.assertRaises(AsrStageError) as context:
            adapter(all_failed).transcribe(duration, input_hash=INPUT_HASH)
        self.assertEqual(context.exception.code, "ASR_FAILED")
        self.assertEqual(len(context.exception.transcript.failures), len(chunks))

    def test_reusable_chunk_checkpoint_skips_backend_call(self) -> None:
        duration = TimeInterval(point(0), point(4500))
        source = utterance("checkpointed", "kept", 100, 300, (word("cw", "kept", 100, 300),))
        backend = DeterministicFixtureBackend((source,))
        running = adapter(backend)
        chunks = ChunkPlanner(running.config).plan(duration, input_hash=INPUT_HASH)
        checkpoint_result = BackendResult((source,), ())
        checkpoint = ChunkCheckpoint(
            chunks[0].chunk_id,
            running._chunk_artifact_hash(chunks[0], INPUT_HASH, checkpoint_result),
            checkpoint_result,
        )
        result = running.transcribe(duration, input_hash=INPUT_HASH, checkpoints={chunks[0].chunk_id: checkpoint})
        self.assertEqual(result.chunks[0]["status"], "skipped")
        self.assertNotIn(chunks[0].chunk_id, backend.calls)
        self.assertEqual(result.chunks[0]["artifact_hash"], checkpoint.artifact_hash)

    def test_timestamp_and_provenance_reject_unsafe_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "NON_REDUCED_TIME_BASE"):
            TimeBase(2, 4)
        with self.assertRaisesRegex(ValueError, "INVALID_CONFIDENCE"):
            VadSegment(point(0), point(10), float("nan"))
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_TIMELINE_CONTRACT"):
            Provenance(
                "p", "1", "b", "m", "1", "runtime", "timeline-v2", CONFIG_HASH, INPUT_HASH, "cpu", "cpu"
            )
        extreme_left = TimePoint((1 << 63) - 1, TimeBase((1 << 64) - 1, (1 << 64) - 3))
        extreme_right = TimePoint((1 << 63) - 2, TimeBase((1 << 64) - 5, (1 << 64) - 7))
        with self.assertRaisesRegex(ValueError, "TIMELINE_OVERFLOW"):
            extreme_left.compare(extreme_right)


if __name__ == "__main__":
    unittest.main()

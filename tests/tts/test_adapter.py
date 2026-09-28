from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint
from engine.dubflow.tts import (
    DeterministicFixtureEngine,
    EngineCapabilities,
    EngineHealth,
    EngineSynthesis,
    LocalTtsAdapter,
    ResourceProfile,
    TtsBackendError,
    TtsCheckpoint,
    TtsConfig,
    TtsError,
    TtsInput,
    TtsProvenance,
    TtsStageError,
    VoiceProfile,
    approved_default_voice,
    map_interval_to_samples,
    map_timepoint_to_sample,
    parse_tts_json,
    validate_tts_document,
)


BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "1" * 64


def point(ticks: int, base: TimeBase = BASE) -> TimePoint:
    return TimePoint(ticks, base)


def segment(identifier: str, start: int = 0, end: int = 1000, text: str = "Xin chào") -> TtsInput:
    return TtsInput(identifier, "source-" + identifier, text, point(start), point(end), source_language="en")


def make_provenance(config: TtsConfig, voice: VoiceProfile | None = None, *, hardware: str = "fixture") -> TtsProvenance:
    voice = voice or approved_default_voice()
    return TtsProvenance(
        "dubflow-tts-test",
        "1.0.0",
        "fixture-tts",
        "python-stdlib",
        "timeline-v1",
        config.content_hash(),
        INPUT_HASH,
        voice.model_id,
        voice.model_version,
        voice.model_hash,
        voice.content_hash(),
        voice.voice_id,
        voice.voice_version,
        config.requested_profile,
        hardware,
        config.resource,
    )


def make_wave(samples: list[int], *, sample_rate: int = 16000, channels: int = 1) -> bytes:
    frames = bytearray()
    for value in samples:
        for _ in range(channels):
            frames.extend(struct.pack("<h", value))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(bytes(frames))
    return stream.getvalue()


class StaticEngine:
    def __init__(self, audio_factory, *, sample_rate: int = 16000, channels: int = 1) -> None:
        self.audio_factory = audio_factory
        self.sample_rate = sample_rate
        self.channels = channels
        self.calls: list[str] = []

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities("static-test", sample_rates=(self.sample_rate,), channels=(self.channels,))

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        return EngineHealth(True)

    def synthesize(self, request) -> EngineSynthesis:
        self.calls.append(request.segment.segment_id)
        return EngineSynthesis(
            self.audio_factory(request),
            sample_rate=self.sample_rate,
            channels=self.channels,
        )


class SelectiveFailureEngine:
    def __init__(self, failed_ids: set[str], *, max_attempts: int = 1) -> None:
        self.failed_ids = failed_ids
        self.fixture = DeterministicFixtureEngine()
        self.max_attempts = max_attempts
        self.calls: list[str] = []

    def capabilities(self) -> EngineCapabilities:
        return self.fixture.capabilities()

    def healthcheck(self, voice: VoiceProfile) -> EngineHealth:
        return EngineHealth(True)

    def synthesize(self, request) -> EngineSynthesis:
        self.calls.append(request.segment.segment_id)
        if request.segment.segment_id in self.failed_ids:
            raise TtsBackendError("MODEL_UNAVAILABLE", "test engine unavailable", retryable=False)
        return self.fixture.synthesize(request)


def adapter(engine, *, config: TtsConfig | None = None, voice: VoiceProfile | None = None, fallback=None, output_dir: str | Path, hardware: str = "fixture") -> LocalTtsAdapter:
    config = config or TtsConfig()
    voice = voice or approved_default_voice()
    return LocalTtsAdapter(
        engine,
        config=config,
        provenance=make_provenance(config, voice, hardware=hardware),
        voice=voice,
        output_dir=output_dir,
        fallback_engine=fallback,
    )


class TtsAdapterTests(unittest.TestCase):
    def test_fixture_preserves_identity_timeline_and_schema_surface(self) -> None:
        with TemporaryDirectory() as directory:
            result = adapter(DeterministicFixtureEngine(), output_dir=directory).synthesize(
                (segment("late", 1100, 1900, "Nguyễn Văn A 2.0!"), segment("early", 100, 900)),
                input_hash=INPUT_HASH,
            )
        self.assertEqual(result.source_segment_ids, ("early", "late"))
        self.assertEqual([item.segment_id for item in result.artifacts], ["early", "late"])
        self.assertEqual(result.artifacts[0].slot_start, point(100))
        self.assertEqual(result.artifacts[0].source_utterance_id, "source-early")
        self.assertGreater(result.artifacts[0].metrics.rms_milli, 0)
        validate_tts_document(result.to_dict())
        parsed = parse_tts_json(result.to_json())
        self.assertEqual(parsed["kind"], "tts_document")
        schema_path = Path(__file__).resolve().parents[2] / "contracts" / "tts" / "schema-v1.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(result.to_dict()), set(schema["required"]))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/tts/schema-v1.json")

    def test_integer_sample_mapping_handles_rational_and_negative_ticks(self) -> None:
        rational = TimeBase(1, 1001)
        self.assertEqual(map_timepoint_to_sample(point(1, rational), 16000), 15)
        self.assertEqual(map_timepoint_to_sample(point(1, rational), 16000, end=True), 16)
        self.assertEqual(map_timepoint_to_sample(point(-1, rational), 16000), -16)
        self.assertEqual(map_timepoint_to_sample(point(-1, rational), 16000, end=True), -15)
        self.assertEqual(map_interval_to_samples(TimeInterval(point(-1, rational), point(0, rational)), 16000), (-16, 0))

    def test_one_sample_fixture_is_non_silent(self) -> None:
        base = TimeBase(1, 16000)
        item = TtsInput("one", "source-one", "x87", point(0, base), point(1, base), source_language="en")
        with TemporaryDirectory() as directory:
            result = adapter(DeterministicFixtureEngine(), output_dir=directory).synthesize((item,), input_hash=INPUT_HASH)
        self.assertEqual(result.artifacts[0].metrics.frame_count, 1)
        self.assertGreater(result.artifacts[0].metrics.rms_milli, 0)

    def test_default_voice_is_approved_offline_and_setup_is_rejected(self) -> None:
        voice = approved_default_voice()
        self.assertTrue(voice.approved and voice.default)
        self.assertFalse(voice.network_required or voice.credential_required)
        with self.assertRaisesRegex(ValueError, "VOICE_REQUIRES_SETUP"):
            VoiceProfile("cloud", "1", network_required=True)

    def test_cpu_profile_and_resource_limits_are_published(self) -> None:
        config = TtsConfig(requested_profile="cpu", resource=ResourceProfile(max_threads=2, max_memory_mb=256, max_batch_items=2))
        with TemporaryDirectory() as directory:
            result = adapter(DeterministicFixtureEngine(), config=config, output_dir=directory, hardware="cpu").synthesize((segment("cpu"),), input_hash=INPUT_HASH)
        self.assertEqual(result.provenance.requested_profile, "cpu")
        self.assertEqual(result.provenance.hardware_profile, "cpu")
        self.assertEqual(result.provenance.resource.max_threads, 2)
        self.assertEqual(result.provenance.resource.max_memory_mb, 256)
        self.assertEqual(result.provenance.resource.max_batch_items, 2)

    def test_audio_validation_rejects_corrupt_silent_wrong_header_clipped_and_wrong_duration(self) -> None:
        cases = {
            "corrupt": lambda request: b"not a wav",
            "silent": lambda request: make_wave([0] * 16000),
            "wrong-header": lambda request: make_wave([1000] * 16000, sample_rate=8000),
            "clipped": lambda request: make_wave([32767] * 16000),
            "duration": lambda request: make_wave([1000] * 8000),
        }
        for name, factory in cases.items():
            with self.subTest(name=name), TemporaryDirectory() as directory:
                config = TtsConfig(max_attempts=1, max_duration_error_ticks=0, max_clip_fraction_ppm=0)
                engine = StaticEngine(factory)
                with self.assertRaises(TtsStageError) as context:
                    adapter(engine, config=config, output_dir=directory).synthesize((segment("bad"),), input_hash=INPUT_HASH)
                codes = {failure.code for failure in context.exception.document.failures}
                self.assertTrue(codes & {"AUDIO_CORRUPT", "AUDIO_SILENT", "AUDIO_METADATA_MISMATCH", "AUDIO_CLIPPING", "AUDIO_DURATION_MISMATCH"})

    def test_partial_failure_isolated_and_all_failure_is_typed(self) -> None:
        config = TtsConfig(max_attempts=1, max_segments_per_chunk=2)
        with TemporaryDirectory() as directory:
            engine = SelectiveFailureEngine({"bad"})
            result = adapter(engine, config=config, output_dir=directory).synthesize((segment("bad"), segment("good", 1200, 2200)), input_hash=INPUT_HASH)
        self.assertEqual([item.segment_id for item in result.artifacts], ["good"])
        self.assertTrue(any(item.segment_id == "bad" for item in result.failures))
        self.assertEqual(result.chunks[0]["status"], "failed")
        with TemporaryDirectory() as directory:
            with self.assertRaises(TtsStageError) as context:
                adapter(SelectiveFailureEngine({"bad"}), config=config, output_dir=directory).synthesize((segment("bad"),), input_hash=INPUT_HASH)
        self.assertEqual(context.exception.document.artifacts, ())
        self.assertTrue(context.exception.document.failures)

    def test_retry_requires_changed_condition(self) -> None:
        class RetryEngine(DeterministicFixtureEngine):
            def __init__(self) -> None:
                super().__init__()
                self.attempt = 0

            def synthesize(self, request):
                self.attempt += 1
                if self.attempt == 1:
                    raise TtsBackendError("TEMPORARY", "change condition", retryable=True)
                return super().synthesize(request)

        with TemporaryDirectory() as directory:
            engine = RetryEngine()
            result = adapter(engine, config=TtsConfig(max_attempts=2), output_dir=directory).synthesize((segment("retry"),), input_hash=INPUT_HASH)
        self.assertEqual(engine.attempt, 2)
        self.assertEqual(result.failures[0].attempt, 1)

        class StuckEngine(DeterministicFixtureEngine):
            def synthesize(self, request):
                raise TtsBackendError("TEMPORARY", "same condition", retryable=True)

        with TemporaryDirectory() as directory:
            with self.assertRaises(TtsStageError) as context:
                adapter(StuckEngine(), config=TtsConfig(max_attempts=2), output_dir=directory).synthesize((segment("stuck"),), input_hash=INPUT_HASH)
        self.assertTrue(any(item.code == "RETRY_CONDITION_UNCHANGED" for item in context.exception.document.failures))

    def test_fallback_records_selected_profile_and_mixed_provenance(self) -> None:
        config = TtsConfig(requested_profile="cpu")
        primary = StaticEngine(lambda request: make_wave([0] * 16000))
        fallback = DeterministicFixtureEngine()
        with TemporaryDirectory() as directory:
            result = adapter(primary, config=config, fallback=fallback, output_dir=directory).synthesize((segment("fallback"),), input_hash=INPUT_HASH)
        self.assertEqual(result.provenance.backend_id, "fallback")
        self.assertEqual(result.provenance.hardware_profile, "cpu")
        self.assertTrue(result.provenance.fallback_reason)
        self.assertTrue(all(item.fallback_used for item in result.failures))

        mixed_primary = SelectiveFailureEngine({"fallback"})
        with TemporaryDirectory() as directory:
            mixed = adapter(mixed_primary, config=config, fallback=DeterministicFixtureEngine(), output_dir=directory).synthesize(
                (segment("primary"), segment("fallback", 1200, 2200)), input_hash=INPUT_HASH
            )
        self.assertEqual(mixed.provenance.hardware_profile, "mixed")
        self.assertEqual(mixed.provenance.backend_id, "composite")

    def test_checkpoint_reuse_and_descriptor_or_config_invalidation(self) -> None:
        config = TtsConfig(max_segments_per_chunk=1)
        with TemporaryDirectory() as directory:
            first_engine = DeterministicFixtureEngine()
            first = adapter(first_engine, config=config, output_dir=directory).synthesize((segment("cached"), segment("fresh", 1200, 2200)), input_hash=INPUT_HASH)
            checkpoints = {
                item.segment_id: TtsCheckpoint(item.segment_id, item.artifact_hash, item)
                for item in first.artifacts
            }
            second_engine = DeterministicFixtureEngine()
            second = adapter(second_engine, config=config, output_dir=directory).synthesize(
                (segment("cached"), segment("fresh", 1200, 2200)), input_hash=INPUT_HASH, checkpoints=checkpoints
            )
            self.assertEqual(second.chunks[0]["status"], "skipped")
            self.assertEqual(second.chunks[1]["status"], "skipped")
            self.assertEqual(second_engine.calls, [])

            tampered = replace(first.artifacts[0], actual_end=point(first.artifacts[0].actual_end.ticks + 1, first.artifacts[0].actual_end.time_base))
            tampered_checkpoint = {"cached": TtsCheckpoint("cached", first.artifacts[0].artifact_hash, tampered)}
            third_engine = DeterministicFixtureEngine()
            adapter(third_engine, config=config, output_dir=directory).synthesize((segment("cached"),), input_hash=INPUT_HASH, checkpoints=tampered_checkpoint)
            self.assertEqual(len(third_engine.calls), 1)

            changed_config = TtsConfig(sample_rate=24000, max_segments_per_chunk=1)
            changed_engine = DeterministicFixtureEngine()
            adapter(changed_engine, config=changed_config, output_dir=directory).synthesize((segment("cached"),), input_hash=INPUT_HASH, checkpoints=checkpoints)
            self.assertEqual(len(changed_engine.calls), 1)

    def test_wire_rejects_duplicates_noncanonical_numbers_and_structural_corruption(self) -> None:
        with TemporaryDirectory() as directory:
            result = adapter(DeterministicFixtureEngine(), output_dir=directory).synthesize((segment("wire"),), input_hash=INPUT_HASH)
        invalid = result.to_dict()
        invalid["source_segment_ids"] = ["wire", "wire"]
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_tts_document(invalid)
        invalid = result.to_dict()
        invalid["artifacts"][0]["slot_end"]["ticks"] = "-0"
        with self.assertRaisesRegex(ValueError, "canonical decimal"):
            validate_tts_document(invalid)
        invalid = result.to_dict()
        invalid["chunks"][0]["segment_ids"] = [["wire"]]
        with self.assertRaisesRegex(ValueError, "segment_id"):
            validate_tts_document(invalid)
        duplicate = result.to_json().replace('"kind":"tts_document"', '"kind":"tts_document","kind":"tts_document"', 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
            parse_tts_json(duplicate)

    def test_provenance_requires_matching_profile_resource_and_model(self) -> None:
        config = TtsConfig(requested_profile="cpu", resource=ResourceProfile(max_threads=2))
        wrong = replace(make_provenance(config), requested_profile="fixture")
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "PROVENANCE_RESOURCE_MISMATCH"):
                LocalTtsAdapter(DeterministicFixtureEngine(), config=config, provenance=wrong, output_dir=directory)


if __name__ == "__main__":
    unittest.main()

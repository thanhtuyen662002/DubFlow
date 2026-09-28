from __future__ import annotations

import io
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.mix import (
    LocalAudioMixer,
    MixConfig,
    MixSegment,
    MixStageError,
    ResourceProfile,
    SourceAudio,
    parse_mix_json,
    validate_mix_document,
)


BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "1" * 64


def point(ticks: int, base: TimeBase = BASE) -> TimePoint:
    return TimePoint(ticks, base)


def make_wave(sample_rate: int, channels: int, frame_count: int, value: int | list[int]) -> bytes:
    if isinstance(value, list):
        values = value
    else:
        values = [value] * frame_count
    if len(values) != frame_count:
        raise AssertionError("fixture sample count does not match frame count")
    frames = bytearray()
    for sample in values:
        frames.extend(struct.pack("<" + "h" * channels, *([sample] * channels)))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(bytes(frames))
    return stream.getvalue()


def read_frames(payload: bytes) -> tuple[int, int, list[tuple[int, ...]]]:
    with wave.open(io.BytesIO(payload), "rb") as reader:
        channels = reader.getnchannels()
        rate = reader.getframerate()
        raw = reader.readframes(reader.getnframes())
    values = list(struct.iter_unpack("<" + "h" * channels, raw))
    return rate, channels, values


def source_audio(*, value: int = 3000, channels: int = 2, frame_count: int = 48000) -> SourceAudio:
    return SourceAudio("source-1", make_wave(48000, channels, frame_count, value), point(0), point(1000), "stereo" if channels == 2 else "mono")


def tts_segment(identifier: str = "s1", *, start: int = 300, end: int = 700, value: int = 5000) -> MixSegment:
    return MixSegment(identifier, "utterance-" + identifier, point(start), point(end), make_wave(16000, 1, (end - start) * 16, value))


class AudioMixTests(unittest.TestCase):
    def test_stereo_layout_ducking_artifacts_and_schema(self) -> None:
        source = source_audio()
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=MixConfig(), output_dir=directory).mix(source, (tts_segment(),), input_hash=INPUT_HASH)
            original = Path(document.original_audio.path).read_bytes()
            final = Path(document.final_mix.path).read_bytes()
            stem = Path(document.dialogue_stem.path).read_bytes()
        self.assertEqual(original, source.audio_bytes)
        self.assertEqual(read_frames(final)[:2], (48000, 2))
        self.assertEqual(read_frames(stem)[:2], (48000, 2))
        _, _, final_frames = read_frames(final)
        self.assertEqual(final_frames[0], (3000, 3000))
        self.assertGreater(final_frames[24000][0], final_frames[0][0])
        self.assertEqual(document.duck_windows[0].start_sample, 14400)
        self.assertEqual(document.duck_windows[0].end_sample, 33600)
        self.assertTrue(document.provenance.non_destructive)
        validate_mix_document(document.to_dict())
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "contracts" / "audio_mix" / "schema-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(set(document.to_dict()), set(schema["required"]))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/audio_mix/schema-v1.json")

    def test_missing_tts_is_explicit_and_preserves_source(self) -> None:
        source = source_audio()
        missing = MixSegment("missing", "utterance-missing", point(300), point(700), None, "missing", "upstream TTS checkpoint unavailable")
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=MixConfig(), output_dir=directory).mix(source, (missing,), input_hash=INPUT_HASH)
            final = Path(document.final_mix.path).read_bytes()
            _, _, stem_frames = read_frames(Path(document.dialogue_stem.path).read_bytes())
        self.assertEqual(final, source.audio_bytes)
        self.assertEqual(document.failures[0].code, "TTS_MISSING")
        self.assertEqual(document.chunks[0]["status"], "failed")
        self.assertTrue(all(frame == (0, 0) for frame in stem_frames))
        self.assertTrue(any("unavailable" in warning for warning in document.warnings))

    def test_corrupt_tts_and_timeline_mismatch_do_not_abort_other_segments(self) -> None:
        bad_audio = MixSegment("bad", "utterance-bad", point(100), point(200), b"not wav")
        mismatched = MixSegment("mismatch", "utterance-mismatch", TimePoint(200, TimeBase(1, 999)), TimePoint(300, TimeBase(1, 999)), make_wave(16000, 1, 1600, 1000))
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=MixConfig(max_segments_per_chunk=3), output_dir=directory).mix(source_audio(), (bad_audio, mismatched, tts_segment("good", start=400, end=500, value=1000)), input_hash=INPUT_HASH)
        self.assertEqual({item.segment_id for item in document.failures}, {"bad", "mismatch"})
        self.assertEqual(document.chunks[0]["status"], "degraded")
        self.assertEqual([item.segment_id for item in document.duck_windows], ["good"])

    def test_final_loudness_and_clipping_are_reduced_without_mutating_original(self) -> None:
        source = source_audio(value=32767)
        config = MixConfig(max_peak=20000, max_rms_milli=500, target_rms_milli=250)
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=config, output_dir=directory).mix(source, (), input_hash=INPUT_HASH)
        self.assertEqual(document.original_audio.metrics.peak, 32767)
        self.assertLessEqual(document.final_mix.metrics.peak, 20000)
        self.assertEqual(document.final_mix.metrics.clipped_samples, 0)
        self.assertTrue(any("peak" in warning or "RMS" in warning for warning in document.warnings))

    def test_source_failure_is_typed_and_duplicate_ids_are_rejected(self) -> None:
        invalid = SourceAudio("source", b"not wav", point(0), point(1000))
        with TemporaryDirectory() as directory:
            with self.assertRaises(MixStageError) as context:
                LocalAudioMixer(config=MixConfig(), output_dir=directory).mix(invalid, (), input_hash=INPUT_HASH)
        self.assertEqual(context.exception.code, "AUDIO_CORRUPT")
        duplicate = tts_segment("same")
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "DUPLICATE_SEGMENT_ID"):
                LocalAudioMixer(config=MixConfig(), output_dir=directory).mix(source_audio(), (duplicate, duplicate), input_hash=INPUT_HASH)

    def test_checkpoint_reuse_and_invalidation_are_hash_bound(self) -> None:
        source = source_audio()
        segment = tts_segment()
        with TemporaryDirectory() as directory:
            mixer = LocalAudioMixer(config=MixConfig(), output_dir=directory)
            first = mixer.mix(source, (segment,), input_hash=INPUT_HASH)
            checkpoint = mixer.checkpoint_for(first, (segment,))
            reused = mixer.mix(source, (segment,), input_hash=INPUT_HASH, checkpoint=checkpoint)
            self.assertEqual(reused.to_json(), first.to_json())
            Path(first.final_mix.path).unlink()
            rebuilt = mixer.mix(source, (segment,), input_hash=INPUT_HASH, checkpoint=checkpoint)
            self.assertTrue(Path(rebuilt.final_mix.path).exists())
            changed = tts_segment(value=4000)
            changed_document = mixer.mix(source, (changed,), input_hash=INPUT_HASH, checkpoint=checkpoint)
        self.assertNotEqual(changed_document.final_mix.content_hash, first.final_mix.content_hash)

    def test_wire_rejects_duplicate_fields_noncanonical_ticks_and_unknown_segments(self) -> None:
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=MixConfig(), output_dir=directory).mix(source_audio(), (tts_segment(),), input_hash=INPUT_HASH)
        invalid = document.to_dict()
        invalid["source_start"]["ticks"] = "-0"
        with self.assertRaisesRegex(ValueError, "canonical decimal"):
            validate_mix_document(invalid)
        invalid = document.to_dict()
        invalid["chunks"][0]["segment_ids"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "ownership"):
            validate_mix_document(invalid)
        duplicate = document.to_json().replace('"kind":"audio_mix_document"', '"kind":"audio_mix_document","kind":"audio_mix_document"', 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
            parse_mix_json(duplicate)

    def test_empty_tts_input_is_a_valid_source_preserving_mix(self) -> None:
        with TemporaryDirectory() as directory:
            document = LocalAudioMixer(config=MixConfig(resource=ResourceProfile(max_threads=2)), output_dir=directory).mix(source_audio(channels=1), (), input_hash=INPUT_HASH)
        self.assertEqual(document.segment_ids, ())
        self.assertEqual(document.chunks, ())
        self.assertEqual(document.channels, 1)
        self.assertEqual(document.provenance.resource.max_threads, 2)


if __name__ == "__main__":
    unittest.main()

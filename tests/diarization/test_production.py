from __future__ import annotations

import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

from engine.dubflow.diarization import (
    ProductionDiarizationConfig,
    ProductionDiarizationError,
    build_speaker_aware_plan,
    diarize_wav,
)
from engine.dubflow.tts.casting import VoiceCaster, VoiceOption


def write_wav(path: Path, *, rate: int = 16_000) -> None:
    samples: list[int] = []
    for seconds, frequency in ((0.7, 180), (0.35, 0), (0.7, 420), (0.35, 0), (0.7, 180)):
        for index in range(int(rate * seconds)):
            samples.append(0 if frequency == 0 else int(9000 * math.sin(2 * math.pi * frequency * index / rate)))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


class ProductionDiarizationTests(unittest.TestCase):
    def test_real_pcm_streaming_backend_emits_stable_clusters_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "actual-source.wav"
            write_wav(source)
            progress: list[tuple[int, int]] = []
            first = diarize_wav(source, "job-real", progress=lambda done, total: progress.append((done, total)))
            second = diarize_wav(source, "job-real")
            first_ids = [segment.speaker_cluster_ids for segment in first.result.segments if segment.role == "speaker"]
            second_ids = [segment.speaker_cluster_ids for segment in second.result.segments if segment.role == "speaker"]
            self.assertGreaterEqual(len(set(item[0] for item in first_ids)), 2)
            self.assertEqual(first_ids, second_ids)
            self.assertEqual(first.result.model_id, "dubflow-cpu-energy-diarizer")
            self.assertEqual(first.result.resource_profile.backend, "pcm-energy-zcr-online-cosine-v1")
            self.assertGreater(first.result.resource_profile.peak_memory_bytes, 0)
            self.assertEqual(first.audio.duration_ticks, "2500" if False else 2800)
            self.assertTrue(first.audio.content_hash.startswith("sha256:"))
            self.assertTrue(progress)
            self.assertIn("overlap_not_inferred_by_single_channel_cpu_backend", first.warnings)

    def test_silence_is_not_promoted_to_a_speaker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "silence.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16_000)
                writer.writeframes(b"\x00\x00" * 16_000)
            report = diarize_wav(source, "job-silent")
            self.assertFalse([item for item in report.result.segments if item.role == "speaker"])

    def test_translated_cues_are_cast_to_cluster_tracks_with_bounded_fit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "actual-source.wav"
            write_wav(source)
            report = diarize_wav(source, "job-plan")
            first = next(segment for segment in report.result.segments if segment.role == "speaker")
            second = next(segment for segment in report.result.segments if segment.role == "speaker" and segment.speaker_cluster_ids != first.speaker_cluster_ids)
            plan = build_speaker_aware_plan(
                (
                    {"segment_id": "cue-a", "start_ticks": first.start_ticks, "end_ticks": first.end_ticks, "translated_text": "Xin chao"},
                    {"segment_id": "cue-b", "start_ticks": second.start_ticks, "end_ticks": second.end_ticks, "translated_text": "Tam biet"},
                ),
                report.result,
                VoiceCaster((VoiceOption("voice-a"), VoiceOption("voice-b"))),
                {"cue-a": first.end_ticks - first.start_ticks, "cue-b": second.end_ticks - second.start_ticks},
            )
        self.assertEqual(plan.fallback_count, 0)
        self.assertEqual(len(plan.multi_voice.assignments), 2)
        self.assertTrue(all(item.fit_mode in {"native", "padded", "speed_adjusted", "rewrite_required"} for item in plan.multi_voice.durations))

    def test_unsupported_and_truncated_audio_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.wav"
            source.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            with self.assertRaises(ProductionDiarizationError) as error:
                diarize_wav(source, "job-bad")
            self.assertIn(error.exception.code, {"AUDIO_CORRUPT", "AUDIO_METADATA_INVALID"})

    def test_duration_policy_rejects_long_audio_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "long.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8_000)
                writer.writeframes(b"\x00\x00" * 16_000)
            with self.assertRaises(ProductionDiarizationError) as error:
                diarize_wav(source, "job-long", config=ProductionDiarizationConfig(max_duration_seconds=0 + 1))
            # The one-second file is valid at the configured limit; ensure a
            # stricter policy is what rejects it rather than WAV parsing.
            self.assertEqual(error.exception.code, "AUDIO_DURATION_TOO_LONG")


if __name__ == "__main__":
    unittest.main()

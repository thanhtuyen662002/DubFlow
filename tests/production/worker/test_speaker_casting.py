from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.dubflow.diarization import SpeakerSegment
from engine.dubflow.worker.b2_audio import B2AudioError
from engine.dubflow.worker.speaker_casting import assign_speaker_voices, plan_identity, speaker_plan, validate_speaker_voices
from engine.dubflow.worker.production_job import TextCue, WorkerConfig, ProductionJobError


ROOT = Path(__file__).resolve().parents[3]
VOICES = ("vi-truc-ly-vieneu3-v1", "vi-thuy-dung-vieneu3-v1")


def segment(number, start, end, confidence="0.8", role="speaker"):
    return SpeakerSegment("seg-" + str(number), start, end,
        ("spk-" + format(number, "016x"),) if role == "speaker" else (), role, Decimal(confidence))


def report(segments):
    return SimpleNamespace(result=SimpleNamespace(segments=segments), warnings=(),
        to_dict=lambda: {"result": {"segments": [item.to_dict() for item in segments]},
            "backend": {"model_id": "fixture"}, "audio": {"content_hash": "sha256:" + "0" * 64}, "vad": {}})


class SpeakerCastingTests(unittest.TestCase):
    def test_duration_ranking_cycles_and_uses_deterministic_ties(self):
        segments = (segment(1, 0, 100), segment(2, 200, 500), segment(3, 600, 800), segment(4, 900, 1000))
        mapping = assign_speaker_voices(segments, VOICES)
        self.assertEqual(list(mapping.values()), [VOICES[0], VOICES[1], VOICES[0], VOICES[1]])
        self.assertEqual(list(mapping), [segments[index].speaker_cluster_ids[0] for index in (1, 2, 0, 3)])
        self.assertEqual(mapping, assign_speaker_voices(tuple(reversed(segments)), VOICES))

    def test_speaking_time_does_not_double_count_overlapping_segments(self):
        mapping = assign_speaker_voices((segment(1, 0, 100), segment(1, 20, 100), segment(2, 200, 350)), VOICES)
        self.assertEqual(mapping[segment(2, 200, 350).speaker_cluster_ids[0]], VOICES[0])

    def test_single_silent_failed_and_missing_audio_fall_back_to_primary(self):
        cues = (TextCue("a", 0, 100, "Hello", "Xin chao"),)
        for segments, reason in (((segment(1, 0, 100),), "SINGLE_SPEAKER"), ((), "NO_SPEAKERS")):
            with self.subTest(reason=reason), patch("engine.dubflow.worker.speaker_casting.diarize_wav", return_value=report(segments)):
                plan = speaker_plan(Path("analysis.wav"), "job", cues, VOICES, "primary")
                self.assertEqual(plan["cue_to_voice"], {"a": "primary"})
                self.assertEqual(plan["fallback_reason"], reason)
                self.assertTrue(plan["warnings"])
        with patch("engine.dubflow.worker.speaker_casting.diarize_wav", side_effect=RuntimeError("failure")):
            self.assertEqual(speaker_plan(Path("bad"), "job", cues, VOICES, "primary")["fallback_reason"], "DIARIZATION_FAILED")
        with patch("engine.dubflow.worker.speaker_casting.diarize_wav") as backend:
            self.assertEqual(speaker_plan(None, "job", cues, VOICES, "primary")["fallback_reason"], "AUDIO_STREAM_MISSING")
            backend.assert_not_called()

    def test_strongest_overlap_keeps_low_confidence_and_unresolved_primary(self):
        segments = (segment(1, 0, 50), segment(2, 50, 250, "0.1"), segment(3, 300, 400, role="unresolved"))
        cues = (TextCue("a", 0, 200, "Hello", "Xin chao"), TextCue("b", 300, 400, "Bye", "Chao"))
        with patch("engine.dubflow.worker.speaker_casting.diarize_wav", return_value=report(segments)):
            plan = speaker_plan(Path("analysis.wav"), "job", cues, VOICES, "primary")
        self.assertEqual(plan["cue_to_voice"], {"a": VOICES[0], "b": "primary"})
        self.assertEqual(plan["cue_speakers"]["a"]["confidence"], "0.1")
        self.assertIsNone(plan["fallback_reason"])
        self.assertTrue(any("SPEAKER_CUE_UNRESOLVED" in warning for warning in plan["warnings"]))
        first = plan_identity(plan)
        plan["diarization"]["resource_profile"] = {"elapsed_ms": 999}
        self.assertEqual(plan_identity(plan), first)
        plan["requested_voices"].reverse()
        self.assertNotEqual(plan_identity(plan), first)

    def test_catalog_validation_and_worker_argument(self):
        self.assertEqual(validate_speaker_voices(list(VOICES), ROOT), VOICES)
        for value in ([], [VOICES[0]], [VOICES[0]] * 2, [True, VOICES[0]], "voices", ["unknown", VOICES[0]]):
            with self.subTest(value=value), self.assertRaises(B2AudioError):
                validate_speaker_voices(value, ROOT)
        args = {"job_id": "job", "stage_id": "local-file", "source_path": str(ROOT / "README.md"),
            "output_dir": str(ROOT / "unused-output"), "app_root": str(ROOT), "model_root": str(ROOT),
            "media_runtime_root": str(ROOT), "ffmpeg_path": str(ROOT / "ffmpeg"), "ffprobe_path": str(ROOT / "ffprobe")}
        self.assertIsNone(WorkerConfig.from_args(args).speaker_voices)
        self.assertEqual(WorkerConfig.from_args(dict(args, speaker_voices=list(VOICES))).speaker_voices, VOICES)
        for value in (True, [], [VOICES[0]], tuple(VOICES), ["unapproved", VOICES[0]]):
            with self.subTest(value=value), self.assertRaises(ProductionJobError):
                WorkerConfig.from_args(dict(args, speaker_voices=value))


if __name__ == "__main__":
    unittest.main()

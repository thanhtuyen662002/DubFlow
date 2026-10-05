"""Observed-format QC regressions; these are not physical/model qualification."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from engine.dubflow.worker.production_job import WorkerConfig, _canonical_qc_report


@dataclass(frozen=True)
class Probe:
    codec: str = "h264"
    audio_codec: str | None = "aac"
    duration_ticks: int | None = 10000

    @property
    def video(self):
        return type("Video", (), {"codec_name": self.codec})()

    @property
    def has_audio(self):
        return self.audio_codec is not None

    @property
    def audio(self):
        return () if self.audio_codec is None else (type("Audio", (), {"codec_name": self.audio_codec})(),)

    def to_dict(self):
        return {"video_codec": self.codec, "audio_codec": self.audio_codec, "duration_ticks": self.duration_ticks}


class CanonicalProductionQCTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "nguon.mp4"
        self.source.write_bytes(b"source bytes")
        self.profile = self.root / "profile.json"
        self.profile.write_text('{"profile":"cpu-v1"}', encoding="utf-8")
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"rendered bytes")
        self.subtitle = self.root / "subtitles.srt"
        self.subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chao\n", encoding="utf-8")
        self.config = WorkerConfig(
            "job-1", "production", self.source, self.root, self.root,
            self.root, self.root, self.root / "ffmpeg.exe", self.root / "ffprobe.exe",
        )
        self.artifacts = {"final_video": self.video, "subtitles_srt": self.subtitle}
        self.source_probe = Probe()
        self.output_probe = Probe()
        self.audio = {"mode": "B1", "hardware_render": {"execution_status": "command_succeeded"}}

    def report(self, **changes):
        values = {
            "config": self.config, "source_probe": self.source_probe,
            "output_probe": self.output_probe, "profile_path": self.profile,
            "artifacts": self.artifacts, "audio": self.audio, "warnings": (),
        }
        values.update(changes)
        return _canonical_qc_report(**values)

    def finding(self, report, code):
        return next(item for item in report["findings"] if item["code"] == code)

    def test_strict_canonical_shape_and_plain_hashes(self):
        report = self.report()
        schema = json.loads((Path(__file__).resolve().parents[2] / "contracts/qc/schema-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(set(report), set(schema["required"]))
        for name in ("provenance", "summary"):
            self.assertEqual(set(report[name]), set(schema["$defs"][name]["required"]))
        finding_schema = schema["$defs"]["finding"]
        finding_keys = set(finding_schema["required"])
        for item in report["findings"]:
            self.assertEqual(set(item), finding_keys)
            self.assertRegex(item["confidence"], finding_schema["properties"]["confidence"]["pattern"])
        hashes = report["provenance"]["upstream_hashes"] + [report["provenance"]["config_hash"], report["provenance"]["threshold_set_hash"]]
        self.assertTrue(hashes)
        for value in hashes:
            self.assertRegex(value, r"^[a-f0-9]{64}$")
        self.assertEqual(report["summary"]["item_count"], len(report["findings"]))
        self.assertEqual(report["summary"]["item_count"], sum(report["summary"][name] for name in ("pass_count", "warn_count", "fail_count")))

    def test_valid_format_still_warns_about_unmeasured_quality(self):
        report = self.report()
        self.assertEqual(report["status"], "WARN")
        finding = self.finding(report, "PRODUCTION_QUALITY_UNMEASURED")
        self.assertEqual((finding["severity"], finding["confidence"]), ("WARN", "0"))
        self.assertEqual(report["provenance"]["calibration_id"], "unmeasured-production-quality-v1")
        self.assertEqual(report["summary"]["low_confidence_count"], 1)
        self.assertEqual(report["summary"]["fail_count"], 0)

    def test_unsupported_video_codec_fails(self):
        report = self.report(output_probe=replace(self.output_probe, codec="hevc"))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(self.finding(report, "QC_VIDEO_CODEC")["severity"], "FAIL")

    def test_lost_source_audio_fails(self):
        report = self.report(output_probe=replace(self.output_probe, audio_codec=None))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(self.finding(report, "QC_AUDIO_MISSING")["confidence"], "1")

    def test_unsupported_audio_codec_fails(self):
        report = self.report(output_probe=replace(self.output_probe, audio_codec="pcm_s16le"))
        self.assertEqual(report["status"], "FAIL")
        self.finding(report, "QC_AUDIO_CODEC")

    def test_silent_source_is_not_fabricated_into_missing_audio(self):
        report = self.report(source_probe=replace(self.source_probe, audio_codec=None), output_probe=replace(self.output_probe, audio_codec=None))
        self.assertEqual(report["status"], "WARN")
        self.assertNotIn("QC_AUDIO_MISSING", {item["code"] for item in report["findings"]})

    def test_duration_tolerance_has_inclusive_boundary(self):
        boundary = self.report(output_probe=replace(self.output_probe, duration_ticks=8000))
        below = self.report(output_probe=replace(self.output_probe, duration_ticks=7999))
        self.assertEqual(boundary["status"], "WARN")
        self.assertEqual(below["status"], "FAIL")
        self.assertEqual(below["summary"]["fail_count"], 1)

    def test_unknown_duration_is_low_confidence_data(self):
        report = self.report(output_probe=replace(self.output_probe, duration_ticks=None))
        self.assertEqual(report["status"], "WARN")
        self.assertEqual(report["summary"]["low_confidence_count"], 2)

    def test_empty_and_missing_artifacts_fail(self):
        self.video.write_bytes(b"")
        report = self.report()
        self.assertEqual(report["status"], "FAIL")
        self.finding(report, "ARTIFACT_EMPTY")
        self.video.unlink()
        report = self.report()
        self.assertEqual(report["status"], "FAIL")
        self.finding(report, "ARTIFACT_READ_FAILED")

    def test_warning_control_characters_are_sanitized_and_bounded(self):
        report = self.report(warnings=("warning\x00with\ncontrols " + "x" * 5000,))
        finding = self.finding(report, "PRODUCER_WARNING")
        self.assertLessEqual(len(finding["message"]), 4096)
        self.assertTrue(all(ord(char) >= 32 for char in finding["message"]))
        self.assertEqual(finding["severity"], "WARN")

    def test_source_artifact_and_profile_bytes_change_provenance(self):
        initial = self.report()
        for path in (self.source, self.video, self.profile):
            before = self.report()
            path.write_bytes(path.read_bytes() + b"changed")
            after = self.report()
            self.assertNotEqual(before["provenance"]["upstream_hashes"], after["provenance"]["upstream_hashes"])
            self.assertNotEqual(before["report_id"], after["report_id"])
        self.assertNotEqual(initial["provenance"]["model_version"], self.report()["provenance"]["model_version"])

    def test_config_probe_and_audio_changes_invalidate_identity(self):
        initial = self.report()
        changed = self.report(config=replace(self.config, burn_in_subtitles=False))
        self.assertNotEqual(initial["provenance"]["config_hash"], changed["provenance"]["config_hash"])
        changed = self.report(output_probe=replace(self.output_probe, duration_ticks=11000))
        self.assertNotEqual(initial["report_id"], changed["report_id"])
        changed = self.report(audio={"mode": "B1", "hardware_render": {"execution_status": "failed"}})
        self.assertNotEqual(initial["report_id"], changed["report_id"])

    def test_identical_observations_have_stable_report_identity(self):
        self.assertEqual(self.report(), self.report())

    def test_profile_version_is_observed_file_hash(self):
        report = self.report()
        self.assertEqual(report["provenance"]["model_version"], hashlib.sha256(self.profile.read_bytes()).hexdigest())
        self.assertEqual(report["provenance"]["producer_version"], "production-local-file-qc-v1")


if __name__ == "__main__":
    unittest.main()

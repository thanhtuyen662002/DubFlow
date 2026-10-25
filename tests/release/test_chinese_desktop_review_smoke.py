"""Harness validation for the real Chinese media smoke; no media, models or runtime needed."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.release import chinese_desktop_review_smoke as smoke


def _good_report() -> dict:
    return {
        "schema": smoke.REPORT_SCHEMA, "verdict": "pass", "real_media": True, "gui": "NOT_RUN: replayed",
        "source": {"sha256": "a" * 64, "size_bytes": 1}, "runtime": {"version": "1.0.0"},
        "stages": [{"name": "production", "command": ["<install>/versions/1.0.0/app/bin/dubflow-supervisor.exe"]}],
        "checks": [{"name": "production: job completed", "passed": True}],
    }


class SourceValidationTests(unittest.TestCase):
    def test_sidecars_match_the_source_name_and_language_tagged_names_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Clip.mp4"
            source.write_bytes(b"x")
            for name in ("clip.srt", "Clip.zh.ass", "Clip.zh.extra.srt", "Clip2.srt", "Clip.txt", "other.vtt"):
                (root / name).write_text("1")
            self.assertEqual([path.name for path in smoke.find_sidecars(source)], ["clip.srt", "Clip.zh.ass"])

    def test_source_must_be_an_absolute_local_video_outside_the_repository_without_sidecar(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "clip.mp4"
            video.write_bytes(b"x")
            self.assertEqual(smoke.validate_source(video, repo_root=root / "repo"), video.resolve())
            cases = {
                Path("clip.mp4"): "SOURCE_INVALID",
                root / "missing.mp4": "SOURCE_INVALID",
                root: "SOURCE_INVALID",
            }
            (root / "notes.txt").write_text("x")
            cases[root / "notes.txt"] = "SOURCE_INVALID"
            for path, code in cases.items():
                with self.subTest(path=path), self.assertRaises(smoke.SmokeError) as raised:
                    smoke.validate_source(path, repo_root=root / "repo")
                self.assertEqual(raised.exception.code, code)
            with self.assertRaises(smoke.SmokeError) as inside:
                smoke.validate_source(video, repo_root=root)
            self.assertEqual(inside.exception.code, "SOURCE_IN_REPOSITORY")
            (root / "clip.zh.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
            with self.assertRaises(smoke.SmokeError) as sidecar:
                smoke.validate_source(video, repo_root=root / "repo")
            self.assertEqual(sidecar.exception.code, "SOURCE_HAS_SIDECAR")

    def test_install_root_must_point_at_a_complete_installed_version(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "current.json").write_text(json.dumps({"version_path": "../elsewhere"}))
            with self.assertRaises(smoke.SmokeError):
                smoke.version_root(root)
            (root / "current.json").write_text(json.dumps({"version_path": "versions/1.0.0"}))
            with self.assertRaisesRegex(smoke.SmokeError, "missing"):
                smoke.version_root(root)
            version = root / "versions" / "1.0.0"
            for required in ("release-manifest.json", "runtime/python.exe", "app/bin/dubflow-supervisor.exe", "runtime/media/ffprobe.exe"):
                (version / required).parent.mkdir(parents=True, exist_ok=True)
                (version / required).write_text("x")
            self.assertEqual(smoke.version_root(root), version)


class ReportValidationTests(unittest.TestCase):
    def test_a_complete_redacted_pass_is_accepted(self) -> None:
        self.assertEqual(smoke.validate_report(_good_report()), [])

    def test_unsafe_or_overclaiming_reports_are_rejected(self) -> None:
        cases = {
            "fixture pass": dict(_good_report(), real_media=False),
            "failed check pass": dict(_good_report(), checks=[{"name": "x", "passed": False}]),
            "empty pass": dict(_good_report(), checks=[]),
            "raw windows path": dict(_good_report(), stages=[{"command": ["C:\\Users\\someone\\clip.mp4"]}]),
            "raw posix home": dict(_good_report(), note="/home/someone/clip.mp4"),
            "bad hash": dict(_good_report(), source={"sha256": "nope"}),
            "unknown verdict": dict(_good_report(), verdict="maybe"),
        }
        for label, report in cases.items():
            with self.subTest(label):
                self.assertNotEqual(smoke.validate_report(report), [])
        missing = _good_report()
        del missing["gui"]
        self.assertIn("missing gui", smoke.validate_report(missing))

    def test_redacted_windows_placeholders_are_not_mistaken_for_paths(self) -> None:
        report = dict(_good_report(), stages=[{"command": [r"<install>\versions\1.0.0\app\bin\dubflow-supervisor.exe"]}])
        self.assertEqual(smoke.validate_report(report), [])
        self.assertNotEqual(smoke.validate_report(dict(_good_report(), note=r"\\server\share\clip.mp4")), [])

    def test_a_failure_report_may_have_failed_checks(self) -> None:
        self.assertEqual(smoke.validate_report(dict(_good_report(), verdict="fail", checks=[{"name": "x", "passed": False}])), [])

    def test_redactor_replaces_every_local_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            redact = smoke.Redactor(source=root / "clip.mp4", work=root / "work")
            value = redact({"cmd": [str(root / "clip.mp4"), str(root / "work" / "run-1"), (root / "work").as_posix() + "/x"]})
            self.assertEqual(value, {"cmd": ["<source>", str(Path("<work>") / "run-1"), "<work>/x"]})


class ReviewContractTests(unittest.TestCase):
    def test_replayed_review_matches_the_host_contract(self) -> None:
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "clip - vi"
            work = output / ".dubflow-work"
            for name, body in (("tts-cache/a.wav", "tts"), ("b2-audio/x/y.wav", "mix"), ("export-pending/final_vi.mp4", "p")):
                (work / name).parent.mkdir(parents=True, exist_ok=True)
                (work / name).write_text(body)
            (output / "final_vi.mp4").write_text("video")
            (output / "job_manifest.json").write_text("{}")
            (work / "translation.json").write_text(json.dumps({"cues": [
                {"cue_id": "c1", "start_ms": 0, "end_ms": 900, "source_text": "你好", "translated_text": "Xin chào"},
                {"cue_id": "c2", "start_ms": 900, "end_ms": 1800, "source_text": "谢谢", "translated_text": "Cảm ơn"},
            ]}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual([cue["text"] for cue in smoke.review_cues(output)], ["Xin chào", "Cảm ơn"])
            for invalid in ({"c1": "  "}, {"": "x"}, {"c1": "x" * 2001}):
                with self.subTest(invalid=invalid), self.assertRaises(smoke.SmokeError):
                    smoke.save_review(output, invalid)
            self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ["clip - vi"], "a rejected save creates nothing")

            first = smoke.save_review(output, {"c2": "Cám ơn nhiều"})
            self.assertEqual(first, output.parent / "clip - vi (sửa 1)")
            self.assertFalse((output / smoke.OVERRIDES_FILE).exists())
            self.assertEqual((output / "final_vi.mp4").read_text(), "video")
            self.assertEqual((first / ".dubflow-work" / "tts-cache" / "a.wav").read_text(), "tts")
            self.assertFalse((first / ".dubflow-work" / "b2-audio").exists() or (first / ".dubflow-work" / "export-pending").exists())
            stored = json.loads((first / smoke.OVERRIDES_FILE).read_text(encoding="utf-8"))
            self.assertEqual(stored, {"schema_version": 1, "cues": {"c2": "Cám ơn nhiều"}})
            link = json.loads((first / smoke.REVISION_OF_FILE).read_text(encoding="utf-8"))
            self.assertEqual((link["original_output_dir"], link["original_manifest_sha256"]),
                             (str(output), "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"))
            cues = {cue["cue_id"]: cue for cue in smoke.review_cues(first)}
            self.assertEqual((cues["c2"]["text"], cues["c2"]["edited"], cues["c1"]["edited"]), ("Cám ơn nhiều", True, False))
            second = smoke.save_review(output, {})
            self.assertEqual(second.name, "clip - vi (sửa 2)")
            self.assertFalse((second / smoke.OVERRIDES_FILE).exists())
            self.assertEqual(smoke.save_review(first, {}).name, "clip - vi (sửa 3)")

    def test_supervisor_command_matches_desktop_start_job_for_chinese(self) -> None:
        version, install, output = Path("V"), Path("I"), Path("O")
        command = smoke.supervisor_command(version, install, "job-1", Path("S.mp4"), output, "vi-voice")
        self.assertEqual(command[1:], ["run", "--root", "V", "--job-id", "job-1", "--source", "S.mp4", "--output-dir", "O",
                                       "--data-root", "I", "--model-root", str(Path("I") / "models"),
                                       "--status-path", str(Path("I") / "control" / "jobs" / "job-1.json"),
                                       "--enable-dubbing", "--tts-voice-id", "vi-voice", "--source-language", "zh"])
        self.assertNotIn("--enable-dubbing", smoke.supervisor_command(version, install, "job-1", Path("S.mp4"), output, None))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from engine.dubflow.product.job import Cancelled, ProductError, probe_duration_ms, run_ffmpeg
from engine.dubflow.reframe import job, render
from engine.dubflow.reframe.__main__ import main

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class ReframeTests(unittest.TestCase):
    def setup_spec(self, root: Path, **changes):
        folder = root / "DubFlow Output" / "Video thử - vi" / "run-test"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "final_vi.mp4"
        if not video.exists():
            video.write_bytes(b"source")
        value = {"schema_version": 1, "video": str(video), "mode": "bang-thong-tin"} | changes
        path = root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return job.load_spec(path)

    def test_strict_validation_and_defaults(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.setup_spec(root)
            self.assertEqual((spec.focus_start, spec.focus_end, spec.start_ms, spec.end_ms), (50, 50, 0, None))
            for mode in render.MODES:
                self.assertEqual(self.setup_spec(root, mode=mode).mode, mode)
            for change in ({"schema_version": True}, {"schema_version": 2}, {"extra": 1}, {"mode": []}, {"mode": "bad"},
                           {"video": "final_vi.mp4"}, {"video": None}, {"title": None}, {"title": "x\n"}, {"title": "đ" * 81},
                           {"summary": "x" * 121}, {"score": True}, {"score": -1}, {"score": 11}, {"score": 1.5},
                           {"pros": ["a"] * 4}, {"cons": [""]}, {"pros": ["x" * 61]}, {"cons": ["a\x85"]}, {"pros": "x"},
                           {"focus_start": True}, {"focus_end": -1}, {"focus_start": 101}, {"focus_end": float("nan")},
                           {"focus_start": float("inf")}, {"start_ms": True}, {"start_ms": 1.5}, {"start_ms": -1},
                           {"start_ms": 9007199254740992}, {"end_ms": 0}, {"end_ms": True}, {"start_ms": 10, "end_ms": 5}):
                with self.subTest(change=change), self.assertRaises(job.ReframeError):
                    self.setup_spec(root, **change)
            for score in (0, 10, None):
                valid = self.setup_spec(root, score=score, title="🌱" * 80, summary="đ" * 120,
                                        pros=["đ" * 60] * 3, cons=["x"] * 3, start_ms=100, end_ms=1500)
                self.assertEqual(valid.score, score)
            outside = root / "final_vi.mp4"
            outside.write_bytes(b"outside")
            with self.assertRaises(job.ReframeError):
                self.setup_spec(root, video=str(outside))
            path = root / "spec.json"
            path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
            with self.assertRaises(job.ReframeError):
                job.load_spec(path)
            path.write_text(" " * (16 * 1024 + 1), encoding="utf-8")
            with self.assertRaises(job.ReframeError):
                job.load_spec(path)

    def test_crop_free_travel_expression_and_encoding(self):
        def y(a, b, t):
            expression = render.crop_y(a, b, 2000).replace("ih", "160").replace("oh", "50.625")
            return eval(expression, {"__builtins__": {}, "min": min}, {"t": t})
        for focus, expected in ((0, 0), (50, 54.6875), (100, 109.375)):
            self.assertAlmostEqual(y(focus, focus, 1), expected)
        for a, b in ((0, 100), (100, 0), (25, 75)):
            for t in (0, 1, 2):
                self.assertAlmostEqual(y(a, b, t), 109.375 * (a + (b - a) * t / 2) / 100)
        argv = render.crop_argv(Path("ffmpeg"), Path("source.mp4"), 500, 2000, 0, 100, True, Path("out.mp4"))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("crop=w=iw:h=iw*9/16:x=0:y=", graph)
        self.assertIn("min(t/2.000,1)", graph)
        self.assertIn("setpts=PTS-STARTPTS,crop", graph)
        self.assertIn("scale=1920:1080", graph)
        self.assertIn("subtitles=panel.ass", graph)
        self.assertIn("[0:a:0]", graph)
        for option in ("h264_mf", "8M", "aac", "48000", "192k"):
            self.assertIn(option, argv)
        self.assertEqual(argv[argv.index("-ss") + 1], "0.500")
        self.assertEqual(argv[argv.index("-ac") + 1], "2")
        self.assertEqual(argv[argv.index("-r") + 1], "30")
        for forbidden in ("hflip", "vflip", "reverse", "atempo", "setpts=0.5"):
            self.assertNotIn(forbidden, graph)
        no_title = render.crop_argv(Path("ffmpeg"), Path("source.mp4"), 0, 2000, 50, 50, False, Path("out.mp4"))
        self.assertNotIn("subtitles", " ".join(no_title))

    def test_shared_panel_summary_and_safe_title_box(self):
        g = render.geometry(90, 160, "bang-thong-tin")
        self.assertGreater(g.x, 1000)
        ass = render.panel_ass("Tiêu đề", 2000, panel_width=g.panel_width, score=7,
                               pros=["Ưu điểm"], cons=["Nhược điểm"], summary="Tóm tắt {\\pos(0,0)}")
        for value in ("PlayResX: 1920", "PlayResY: 1080", "7/10", "✓ Ưu điểm", "✗ Nhược điểm", "Tóm tắt", "0:00:02.00"):
            self.assertIn(value, ass)
        self.assertNotIn(r"{\pos(0,0)}", ass)
        self.assertNotIn("PHẦN 0", ass)
        title = render.title_ass("Hộp nhỏ {\\pos(0,0)}", 2000)
        self.assertIn("pos(40,35)", title)
        self.assertIn(r"\p1", title)
        self.assertNotIn(r"{\pos(0,0)}", title)
        self.assertNotIn("Dialogue:", render.title_ass("", 2000))

    def test_atomic_publish_receipts_and_verification(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory), start_ms=500, end_ms=2000, summary="Tóm tắt")
            final = spec.video.parent / "final_vi_16x9.mp4"
            work = spec.video.parent / ".dubflow-work/reframe"
            calls = []
            def runner(argv, cwd, cancelled):
                self.assertFalse(cancelled())
                self.assertTrue((cwd / "panel.ass").is_file())
                calls.append(argv)
                Path(argv[-1]).write_bytes(b"rendered")
            def probe(path):
                return 3000 if path == spec.video else 1500
            def measure(path):
                return render.Video(90, 160, "25") if path == spec.video else render.Video(1920, 1080, "30")
            def run(**changes):
                return job.run(spec, **({"probe": probe, "measure": measure, "ffmpeg": Path("ffmpeg"), "ffmpeg_runner": runner} | changes))
            self.assertEqual(run()["state"], "COMPLETED")
            self.assertEqual(final.read_bytes(), b"rendered")
            self.assertEqual(spec.video.read_bytes(), b"source")
            self.assertEqual(run()["state"], "COMPLETED")
            self.assertEqual(len(calls), 1)
            final.write_bytes(b"damaged")
            self.assertEqual(run()["state"], "COMPLETED")
            self.assertEqual(len(calls), 2)
            self.assertEqual(run(encoder="mpeg4")["state"], "COMPLETED")
            self.assertEqual(len(calls), 3)
            final.write_bytes(b"previous good export")
            for change in ({"probe": lambda p: 3000 if p == spec.video else 700},
                           {"measure": lambda p: measure(p) if p == spec.video else render.Video(1280, 720, "30")},
                           {"measure": lambda p: measure(p) if p == spec.video else render.Video(1920, 1080, "25")}):
                self.assertEqual(run(**change)["state"], "FAILED")
                self.assertEqual(final.read_bytes(), b"previous good export")
                self.assertFalse((work / "reframe.partial.mp4").exists())
            def broken(*_):
                raise ProductError("diagnostic with private input")
            failure = run(ffmpeg_runner=broken)
            self.assertEqual(failure["state"], "FAILED")
            self.assertNotIn("private input", failure["message"])

    def test_landscape_square_and_outside_trim_refused(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory))
            calls = []
            def run(current, width=160, height=90):
                return job.run(current, probe=lambda _: 2000, measure=lambda _: render.Video(width, height, "30"),
                               ffmpeg=Path("ffmpeg"), ffmpeg_runner=lambda *_: calls.append(True))
            for width, height in ((160, 90), (90, 90)):
                status = run(spec, width, height)
                self.assertEqual(status["state"], "FAILED")
                self.assertIn("video dọc", status["message"])
            for current in (replace(spec, start_ms=2000), replace(spec, end_ms=2001)):
                self.assertIn("Mốc cắt", run(current, 90, 160)["message"])
            self.assertFalse(calls)

    def test_cancel_before_during_and_child_is_killed(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory), mode="cat-ngang")
            work = spec.video.parent / ".dubflow-work/reframe"
            final = spec.video.parent / "final_vi_16x9.mp4"
            final.write_bytes(b"previous")
            def runner(argv, cwd, cancelled):
                Path(argv[-1]).write_bytes(b"partial")
                (cwd / "cancel").touch()
                self.assertTrue(cancelled())
                raise Cancelled()
            def run():
                return job.run(spec, probe=lambda _: 2000, measure=lambda _: render.Video(90, 160, "30"),
                               ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner)
            self.assertEqual(run()["state"], "CANCELLED")
            self.assertEqual(run()["state"], "CANCELLED")
            self.assertEqual(final.read_bytes(), b"previous")
            self.assertFalse((work / "reframe.partial.mp4").exists())
            started = time.monotonic()
            with self.assertRaises(Cancelled):
                run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], work,
                           lambda: time.monotonic() - started > 0.1, poll_seconds=0.05)
            self.assertLess(time.monotonic() - started, 5)

    def test_cli_ascii_last_line_and_invalid_spec(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.setup_spec(root)
            (root / "spec.json").write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--spec", str(root / "spec.json"), "--output-dir", str(spec.video.parent),
                             "--ffmpeg", "unused", "--ffprobe", "unused"])
            line = output.getvalue().splitlines()[-1]
            self.assertTrue(line.isascii())
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(line)["state"], "FAILED")

    @unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/FFprobe unavailable")
    def test_real_ffmpeg_both_modes(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory), start_ms=200, end_ms=1700, title="Tiêu đề thử", score=8,
                                   pros=["Ưu điểm"], cons=["Nhược điểm"], summary="Tóm tắt video", focus_start=0, focus_end=100)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=90x160:r=30",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
                            "-c:v", "mpeg4", "-c:a", "aac", str(spec.video)], check=True, capture_output=True)
            original = hashlib.sha256(spec.video.read_bytes()).digest()
            for mode in render.MODES:
                with self.subTest(mode=mode):
                    status = job.run(replace(spec, mode=mode), probe=lambda p: probe_duration_ms(Path(FFPROBE), p),
                                     measure=lambda p: job.probe_video(Path(FFPROBE), p), ffmpeg=Path(FFMPEG), encoder="mpeg4")
                    self.assertEqual(status["state"], "COMPLETED", status["message"])
                    path = Path(status["path"])
                    video = job.probe_video(Path(FFPROBE), path)
                    self.assertEqual((video.width, video.height, Fraction(video.fps)), (1920, 1080, 30))
                    self.assertLessEqual(abs(probe_duration_ms(Path(FFPROBE), path) - 1500), 300)
                    streams = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)],
                                                        check=True, capture_output=True).stdout)["streams"]
                    audio = next(s for s in streams if s["codec_type"] == "audio")
                    self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))
                    self.assertEqual(hashlib.sha256(spec.video.read_bytes()).digest(), original)


if __name__ == "__main__":
    unittest.main()

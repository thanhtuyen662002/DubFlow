from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from engine.dubflow.product.job import Cancelled, probe_duration_ms, run_ffmpeg
from engine.dubflow.shorts import job, render

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class ShortsTests(unittest.TestCase):
    def setup_spec(self, root, **changes):
        folder = root / "DubFlow Output" / "Video thử - vi" / "run-test"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "final_vi.mp4"
        if not video.exists():
            video.write_bytes(b"source")
        value = {"schema_version": 1, "video": str(video), "items": [{"start_ms": 500, "end_ms": 5500, "title": "Đoạn hay"}], "layout": "auto"}
        value.update(changes)
        path = root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return job.load_spec(path)

    def test_strict_spec(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.setup_spec(root).items[0].start_ms, 500)
            for mode in render.LAYOUTS:
                self.assertEqual(self.setup_spec(root, layout=mode).layout, mode)
            for changes in ({"schema_version": True}, {"schema_version": 2}, {"layout": "bad"}, {"layout": []},
                            {"video": "final_vi.mp4"}, {"video": None}, {"items": []}, {"items": [None]},
                            {"extra": 1}, {"items": [dict(start_ms=0, end_ms=5000, title="X")] * 4}):
                with self.subTest(changes=changes), self.assertRaises(job.ShortsError):
                    self.setup_spec(root, **changes)
            for changes in ({"start_ms": True}, {"start_ms": 0.5}, {"start_ms": -1}, {"end_ms": 4999},
                            {"end_ms": 60001}, {"end_ms": 0}, {"title": ""}, {"title": " "}, {"title": "đ" * 81},
                            {"title": "\x85"}, {"title": "x\n"}, {"title": None}, {"extra": 1}):
                item = dict(start_ms=0, end_ms=5000, title="Hay") | changes
                with self.subTest(item=item), self.assertRaises(job.ShortsError):
                    self.setup_spec(root, items=[item])
            self.assertEqual(len(self.setup_spec(root, items=[dict(start_ms=0, end_ms=60000, title="đ" * 80)]).items[0].title), 80)
            other = root / "final_vi.mp4"
            other.write_bytes(b"outside")
            with self.assertRaises(job.ShortsError):
                self.setup_spec(root, video=str(other))

    def test_geometry_and_argv(self):
        landscape = render.Video(1920, 1080, "25")
        portrait = render.Video(720, 1280, "30")
        self.assertEqual(render.effective_layout(landscape, "auto"), "ban-tin")
        self.assertEqual(render.effective_layout(portrait, "mo-nen"), "vertical")
        self.assertIn("scale=1080:1920", render.geometry(portrait, "auto"))
        self.assertNotIn("gblur", render.geometry(portrait, "auto"), "an exact 9:16 source fills the frame")
        four_five = render.geometry(render.Video(1080, 1350, "30"), "auto")
        self.assertIn("force_original_aspect_ratio=decrease", four_five)
        self.assertIn("gblur", four_five, "a 4:5 source is fitted on a blurred fill, never stretched")
        panel = render.geometry(landscape, "auto")
        self.assertIn("scale=1080:-2", panel)
        self.assertIn("pad=1080:1200:0:(oh-ih)/2", panel)
        self.assertIn("pad=1080:1920:0:0:color=0x0E7A3B", panel)
        blur = render.geometry(landscape, "mo-nen")
        self.assertIn("gblur", blur)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", blur)
        argv = render.ffmpeg_argv(Path("ffmpeg"), Path("final_vi.mp4"), landscape, "auto", 1000, 36000, Path("out.mp4"))
        self.assertEqual(argv[argv.index("-ss") + 1], "1.000")
        self.assertEqual(argv[argv.index("-t") + 1], "35.000")
        for option in ("h264_mf", "6000000", "aac", "48000", "192k", "0:a:0?"):
            self.assertIn(option, argv)
        self.assertEqual(argv[argv.index("-ac") + 1], "2")
        self.assertEqual(argv[argv.index("-r") + 1], "30")
        self.assertNotIn("hflip", " ".join(argv))
        self.assertNotIn("atempo", " ".join(argv))

    def test_title_ass_and_description(self):
        ass = render.ass_document("Một tiêu đề đủ dài cần xuống dòng {\\pos(0,0)}", 35000, "ban-tin")
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn("Arial,66", ass)
        self.assertIn(r"\N", ass)
        self.assertIn("0:00:35.00", ass)
        self.assertIn("pos(540,1310)", ass)
        self.assertNotIn(r"{\pos(0,0)}", ass)
        self.assertIn("Xem bản đầy đủ trên kênh", ass)
        top = render.ass_document("Xin chào", 5000, "vertical")
        self.assertIn("pos(540,100)", top)
        self.assertIn(",3,18,0,8,", top)
        credit = dict(creator=" Tác giả ", platform="Bilibili", url="https://example.com/video", rights="permission", note="private")
        self.assertEqual(job.description("Hay", "Video", credit), "Hay\nXem bản đầy đủ: Video trên kênh.\nNguồn: Tác giả (Bilibili) – https://example.com/video\n")
        self.assertEqual(job.description("Hay", "Video", {}), "Hay\nXem bản đầy đủ: Video trên kênh.\n")
        self.assertNotIn("private", job.description("Hay", "Video", credit))
        self.assertIn("Nguồn: Tác  giả", job.description("Hay", "Video", {"creator": "  Tác  giả  "}))
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory))
            (spec.video.parent / "job_manifest.json").write_text(json.dumps({"source": {"path": "C:/private-folder/Tiêu đề đầy đủ.mp4"}}), encoding="utf-8")
            self.assertEqual(job.video_title(spec.video), "Tiêu đề đầy đủ")

    def test_atomic_resume_failure_duration_and_cancellation(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory))
            work = spec.video.parent / ".dubflow-work/shorts"
            final = spec.video.parent / "shorts/short-1.mp4"
            calls = []

            def runner(argv, cwd, cancelled):
                self.assertTrue(cancelled is not None)
                calls.append(argv)
                Path(argv[-1]).write_bytes(b"rendered")

            def probe(path):
                return 10000 if path == spec.video else 5000

            def measure(path):
                return render.Video(320, 180, "30") if path == spec.video else render.Video(1080, 1920, "30")

            def run(**kwargs):
                return job.run(spec, probe=probe, measure=measure, ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner, **kwargs)

            (spec.video.parent / "source_credit.json").write_text(json.dumps(dict(creator="A", platform="B", url="U")), encoding="utf-8")
            result = run()
            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual((result["done"], result["total"]), (1, 1))
            self.assertEqual(final.read_bytes(), b"rendered")
            self.assertIn("Video thử", result["outputs"][0]["text"])
            self.assertIn("Nguồn: A (B) – U", final.with_suffix(".txt").read_text(encoding="utf-8"))
            self.assertEqual(run()["state"], "COMPLETED")
            self.assertEqual(len(calls), 1, "verified completed clip is reused")
            final.write_bytes(b"corrupt")
            self.assertEqual(run()["state"], "COMPLETED")
            self.assertEqual(len(calls), 2)
            invalid = replace(spec, items=(job.Item(0, 11000, "Outside"),))
            self.assertEqual(job.run(invalid, probe=probe, measure=measure, ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner)["state"], "FAILED")
            final.write_bytes(b"previous good export")
            wrong = job.run(spec, probe=lambda p: 10000 if p == spec.video else 2000, measure=measure,
                            ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner)
            self.assertEqual(wrong["state"], "FAILED")
            self.assertEqual(final.read_bytes(), b"previous good export")
            self.assertFalse((work / "short.partial.mp4").exists())

            def cancelling(argv, cwd, cancelled):
                Path(argv[-1]).write_bytes(b"partial")
                (cwd / "cancel").touch()
                self.assertTrue(cancelled())
                raise Cancelled()

            cancelled = job.run(spec, probe=probe, measure=measure, ffmpeg=Path("ffmpeg"), ffmpeg_runner=cancelling)
            self.assertEqual(cancelled["state"], "CANCELLED")
            self.assertEqual(final.read_bytes(), b"previous good export")
            self.assertFalse((work / "short.partial.mp4").exists())
            self.assertEqual(run()["state"], "CANCELLED")

    def test_cancel_keeps_completed_clip(self):
        with TemporaryDirectory() as directory:
            spec = self.setup_spec(Path(directory), items=[dict(start_ms=0, end_ms=5000, title="One"), dict(start_ms=5000, end_ms=10000, title="Two")])
            calls = []

            def runner(argv, cwd, cancelled):
                calls.append(argv)
                Path(argv[-1]).write_bytes(b"video")
                if len(calls) == 2:
                    (cwd / "cancel").touch()

            result = job.run(spec, probe=lambda p: 10000 if p == spec.video else 5000,
                             measure=lambda p: render.Video(1080, 1920, "30"), ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner)
            self.assertEqual(result["state"], "CANCELLED")
            self.assertEqual(result["done"], 1)
            self.assertEqual(len(result["outputs"]), 1)
            self.assertTrue((spec.video.parent / "shorts/short-1.mp4").is_file())
            self.assertFalse((spec.video.parent / "shorts/short-2.mp4").exists())

    def test_runner_kills_child_on_cancel(self):
        with TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaises(Cancelled):
                run_ffmpeg([sys.executable, "-c", "import time; time.sleep(30)"], Path(directory),
                           lambda: time.monotonic() - started > 0.1, poll_seconds=0.05)
            self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/FFprobe unavailable")
    def test_real_ffmpeg_landscape_and_portrait(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for size, mode in (("160x90", "auto"), ("160x90", "mo-nen"), ("90x160", "auto")):
                with self.subTest(size=size, layout=mode):
                    case = root / f"{size}-{mode}"
                    case.mkdir()
                    spec = self.setup_spec(case, layout=mode)
                    # Tiny generated fixtures exist only in this temporary fake output.
                    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={size}:r=30",
                                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "6",
                                    "-c:v", "mpeg4", "-c:a", "aac", str(spec.video)], check=True, capture_output=True)
                    result = job.run(spec, probe=lambda p: probe_duration_ms(Path(FFPROBE), p),
                                     measure=lambda p: job.probe_video(Path(FFPROBE), p), ffmpeg=Path(FFMPEG), encoder="mpeg4")
                    self.assertEqual(result["state"], "COMPLETED", result["message"])
                    for output in result["outputs"]:
                        path = Path(output["path"])
                        video = job.probe_video(Path(FFPROBE), path)
                        self.assertEqual((video.width, video.height, Fraction(video.fps)), (1080, 1920, 30))
                        self.assertLessEqual(abs(probe_duration_ms(Path(FFPROBE), path) - 5000), 300)
                        streams = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(path)], check=True, capture_output=True).stdout)["streams"]
                        audio = next(s for s in streams if s["codec_type"] == "audio")
                        self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))


if __name__ == "__main__":
    unittest.main()

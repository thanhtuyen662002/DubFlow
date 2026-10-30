from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
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

from engine.dubflow.compilation import job, layout, render
from engine.dubflow.compilation import __main__ as cli
from engine.dubflow.product.job import Cancelled, probe_duration_ms, run_ffmpeg
from engine.dubflow.product.script import Caption

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class FakeSpeaker:
    sample_rate = 48000

    def __init__(self):
        self.closed = False

    def speak(self, text):
        return [0.05] * 9600

    def close(self):
        self.closed = True


class CompilationTests(unittest.TestCase):
    def value(self, root):
        items = []
        for number in (1, 2):
            folder = root / "DubFlow Output" / f"job-{number}"
            folder.mkdir(parents=True, exist_ok=True)
            video = folder / "final_vi.mp4"
            if not video.exists():
                video.write_bytes(b"video")
            items.append(dict(video=str(video), title=f"Clip {number}", lead="Nhận xét này.", score=8,
                              pros=["Nội dung rõ"], cons=["Chưa đủ thông tin"], start_ms=None, end_ms=None))
        return dict(schema_version=1, title="Tổng hợp thử", layout="bang-thong-tin", voice_id="vi-test", speed=1.1,
                    music=None, music_volume=0.2, intro="Xin chào.", outro="Hẹn gặp lại.", items=items)

    def spec(self, root, value=None):
        path = root / "spec.json"
        path.write_text(json.dumps(value if value is not None else self.value(root)), encoding="utf-8")
        return job.load_spec(path)

    def test_strict_spec_validation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            value = self.value(root)
            self.assertEqual(len(self.spec(root, value).items), 2)
            for field, change in (("schema_version", True), ("schema_version", 2), ("title", ""), ("title", "đ" * 121),
                                  ("intro", "đ" * 801), ("outro", "!"), ("intro", "\x85"), ("intro", None),
                                  ("voice_id", ""), ("speed", True), ("speed", float("nan")), ("speed", 1.31),
                                  ("music_volume", float("inf")), ("music_volume", -1), ("music_volume", False),
                                  ("music", "relative.mp3"), ("layout", "bad"), ("items", []), ("items", [value["items"][0]]),
                                  ("items", value["items"] * 16), ("extra", 1)):
                with self.subTest(field=field, change=change), self.assertRaises(job.CompilationError):
                    self.spec(root, {**value, field: change})
            for field, change in (("title", "x" * 81), ("lead", "x" * 401), ("lead", "\0"), ("lead", "!!!"),
                                  ("score", True), ("score", 1.1), ("score", 11), ("pros", ["x"] * 4), ("cons", ["x" * 61]),
                                  ("cons", [None]), ("start_ms", -1), ("start_ms", True), ("end_ms", 0), ("end_ms", 0.1),
                                  ("video", "relative/final_vi.mp4"), ("extra", 1)):
                bad = {**value, "items": [{**value["items"][0], field: change}, value["items"][1]]}
                with self.subTest(field=field, change=change), self.assertRaisesRegex(job.CompilationError, "Phần 1"):
                    self.spec(root, bad)
            outside = root / "final_vi.mp4"
            outside.write_bytes(b"video")
            with self.assertRaises(job.CompilationError):
                self.spec(root, {**value, "items": [{**value["items"][0], "video": str(outside)}] * 2})
            self.assertEqual(len(self.spec(root, {**value, "intro": "đ" * 800}).intro), 800)

    def test_cli_validation_writes_status_and_last_ascii_json(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "invalid.json"
            request.write_text('{"schema_version":2}', encoding="utf-8")
            argv = ["--spec", str(request)]
            for name in ("output-dir", "app-root", "model-root", "ffmpeg", "ffprobe"):
                argv.extend((f"--{name}", str(root)))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(cli.main(argv), 1)
            line = stdout.getvalue().splitlines()[-1]
            self.assertTrue(line.isascii())
            status = json.loads(line)
            self.assertEqual(status["state"], "FAILED")
            self.assertEqual(json.loads((root / ".dubflow-work/status.json").read_text(encoding="utf-8")), status)

    def test_geometry_panel_ass_and_safe_text(self):
        vertical = layout.geometry(90, 160, "bang-thong-tin")
        self.assertEqual((vertical.height, vertical.x), (1080, 1920 - vertical.width - 60))
        self.assertGreater(vertical.panel_width, 1000)
        landscape = layout.geometry(160, 90, "bang-thong-tin")
        self.assertEqual((landscape.width, landscape.height, landscape.x, landscape.panel_width), (1920, 1080, 0, 0))
        full = layout.geometry(90, 160, "toan-man-hinh")
        self.assertEqual(full.x, (1920 - full.width) // 2)
        self.assertEqual(full.panel_width, 0)
        ass = layout.ass_document(r"Tên {\p1}", 2500, number=2, panel_width=vertical.panel_width, score=8,
                                  pros=["Rõ ràng"], cons=["Còn ngắn"], captions=[Caption(300, 900, "Xin chào.")], card=True)
        for expected in ("PlayResX: 1920", "PlayResY: 1080", "PHẦN 2", "8/10", "✓ Rõ ràng", "✗ Còn ngắn", "0:00:00.30,0:00:00.90", "352A17"):
            self.assertIn(expected, ass)
        self.assertNotIn(r"Tên {\p1}", ass)
        self.assertIn("Phần 2: Tên", layout.ass_document("Tên", 1000, number=2, score=8))
        argv = render.clip_argv(Path("ffmpeg"), Path("clip.mp4"), render.Video(90, 160, "25"), "toan-man-hinh", 250, 1000,
                                "clip.ass", Path("out.mp4"))
        graph = argv[argv.index("-filter_complex") + 1]
        for expected in ("split=2", "boxblur", f"scale={full.width}:{full.height}", "overlay=", "fps=30", "48000", "apad"):
            self.assertIn(expected, graph)
        for forbidden in ("hflip", "vflip", "reverse", "zoompan"):
            self.assertNotIn(forbidden, graph)

    def test_chapters_and_source_description(self):
        segments = [("Mở đầu", 2300), ("Phần 1: A", 1200), ("", 80000), ("Phần 2: B", 3600000), ("Kết thúc", 1000)]
        chapters = job.chapters(segments)
        self.assertEqual(chapters, "00:00 Mở đầu\n00:02 Phần 1: A\n01:23 Phần 2: B\n01:01:23 Kết thúc\n")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.spec(root)
            credit = spec.items[0].video.parent / "source_credit.json"
            credit.write_text(json.dumps(dict(schema_version=1, creator="Tác giả", platform="Bilibili", url="https://example.org/video", rights="permission", note="private note")), encoding="utf-8")
            description = job.description(chapters, spec.items)
            self.assertIn("Nguồn: Tác giả (Bilibili) – https://example.org/video", description)
            self.assertNotIn("private note", description)
            self.assertTrue(description.startswith(chapters))
            credit.write_text("invalid", encoding="utf-8")
            with self.assertRaisesRegex(job.CompilationError, "Phần 1: Clip 1"):
                job.description(chapters, spec.items)

    def test_music_only_in_narration_commands(self):
        card = render.card_argv(Path("ffmpeg"), Path("still.png"), Path("voice.wav"), Path("music.mp3"), .25, 1200, "card.ass", Path("out"))
        self.assertIn("music.mp3", card)
        self.assertIn("-stream_loop", card)
        self.assertIn("volume=0.2500", card[card.index("-filter_complex") + 1])
        clip = render.clip_argv(Path("ffmpeg"), Path("final_vi.mp4"), render.Video(160, 90, "30"), "bang-thong-tin", 0, 1000, "clip.ass", Path("out"))
        self.assertNotIn("music.mp3", clip)
        self.assertNotIn("amix", clip[clip.index("-filter_complex") + 1])
        for command in (clip, card):
            self.assertIn("8M", command)
            self.assertIn("192k", command)
            self.assertIn("48000", command)

    def fake_run(self, spec, output, *, fail=False, cancel=False, bad_duration=False):
        calls, durations = [], {}
        speaker = FakeSpeaker()
        def runner(argv, cwd, cancelled):
            calls.append(argv)
            path = Path(argv[-1])
            if fail:
                raise job.CompilationError("FFmpeg lỗi")
            path.write_bytes(path.name.encode())
            if "-t" in argv:
                durations[path.name] = round(float(argv[argv.index("-t") + 1]) * 1000)
            else:
                durations[path.name] = sum(durations.values())
            if cancel:
                (cwd / "cancel").touch()
                self.assertTrue(cancelled())
        def probe(path):
            if path.name == "final_vi.mp4":
                return 1000
            if path.name == "compilation.partial.mp4":
                receipts = [json.loads(p.read_text()) for p in (output / ".dubflow-work").glob("*.receipt.json")]
                return sum(r["duration_ms"] for r in receipts) + (10000 if bad_duration else 0)
            return durations[path.name]
        result = job.run(spec, output, speaker=lambda status: speaker, probe=probe,
                         measure=lambda path: render.VIDEO, ffmpeg=Path("ffmpeg"), ffmpeg_runner=runner)
        return result, calls, speaker

    def test_run_checkpoints_cancel_failure_and_atomic_publish(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec, output = self.spec(root), root / "result"
            result, calls, speaker = self.fake_run(spec, output)
            self.assertEqual(result["state"], "COMPLETED", result.get("message"))
            self.assertEqual((result["done"], result["total"]), (6, 6))
            self.assertTrue(speaker.closed)
            self.assertTrue((output / "description.txt").is_file())
            again, reused, unused = self.fake_run(spec, output)
            self.assertEqual(again["state"], "COMPLETED")
            self.assertEqual(len(reused), 1, "verified segments need only concatenation")
            self.assertFalse(unused.closed, "speaker was never started")
            spec.items[1].video.write_bytes(b"changed source")
            refreshed, changed_calls, _ = self.fake_run(spec, output)
            self.assertEqual(refreshed["state"], "COMPLETED")
            self.assertTrue(any(str(spec.items[1].video) in call for call in changed_calls))
            self.assertFalse(any(str(spec.items[0].video) in call for call in changed_calls), "unchanged source keeps its segments")
            (output / ".dubflow-work/clip-2.mp4").write_bytes(b"corrupt")
            failed, _, _ = self.fake_run(spec, output, fail=True)
            self.assertEqual(failed["state"], "FAILED")
            self.assertIn("Phần 2: Clip 2", failed["message"])
            self.assertTrue((output / "compilation.mp4").is_file())
            cancelled, _, _ = self.fake_run(spec, output, cancel=True)
            self.assertEqual(cancelled["state"], "CANCELLED")
            (output / ".dubflow-work/cancel").unlink()
            wrong, _, _ = self.fake_run(spec, output, bad_duration=True)
            self.assertEqual(wrong["state"], "FAILED")
            self.assertIn("Thời lượng", wrong["message"])
            self.assertFalse((output / ".dubflow-work/compilation.partial.mp4").exists())

    def test_invalid_trim_names_item_and_pre_cancel_never_starts_speaker(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self.spec(root)
            bad = replace(spec, items=(replace(spec.items[0], end_ms=2000), spec.items[1]))
            result, _, _ = self.fake_run(bad, root / "result")
            self.assertEqual(result["state"], "FAILED")
            self.assertIn("Phần 1: Clip 1", result["message"])
            work = root / "cancelled/.dubflow-work"
            work.mkdir(parents=True)
            (work / "cancel").touch()
            result, calls, voice = self.fake_run(spec, work.parent)
            self.assertEqual(result["state"], "CANCELLED")
            self.assertFalse(calls)
            self.assertFalse(voice.closed)

    def test_cancel_kills_running_process(self):
        with TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaises(Cancelled):
                run_ffmpeg([sys.executable, "-c", "import time; time.sleep(20)"], Path(directory),
                           lambda: time.monotonic() - started > .2, poll_seconds=.02)
            self.assertLess(time.monotonic() - started, 5)

    @unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/ffprobe not installed")
    def test_real_ffmpeg_end_to_end(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            value = self.value(root)
            for item, size in zip(value["items"], ("90x160", "160x90")):
                subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=25:d=0.8",
                                "-f", "lavfi", "-i", "sine=frequency=400:sample_rate=48000:duration=0.8", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", item["video"]],
                               check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value["items"][1].update(start_ms=100, end_ms=600)
            music = root / "music.wav"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=900:duration=0.2", str(music)],
                           check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value["music"] = str(music)
            spec, output = self.spec(root, value), root / "result"
            def checked_ffmpeg(argv, cwd, cancelled):
                try:
                    run_ffmpeg(argv, cwd, cancelled)
                except job.CompilationError as error:
                    detail = (cwd / "ffmpeg-error.log").read_text(encoding="utf-8", errors="replace")
                    raise job.CompilationError(detail.replace(str(root), "<fixture>")[:2000]) from error
            result = job.run(spec, output, speaker=lambda status: FakeSpeaker(),
                             probe=lambda path: probe_duration_ms(Path(FFPROBE), path),
                             measure=lambda path: job.probe_video(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4", ffmpeg_runner=checked_ffmpeg)
            self.assertEqual(result["state"], "COMPLETED", result.get("message"))
            final = output / "compilation.mp4"
            video = job.probe_video(Path(FFPROBE), final)
            self.assertEqual((video.width, video.height, video.fps), (1920, 1080, "30/1"))
            self.assertLess(abs(probe_duration_ms(Path(FFPROBE), final) - result["duration_ms"]), 300)
            self.assertEqual(result["done"], 6)
            self.assertIn("Phần 2: Clip 2", (output / "chapters.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

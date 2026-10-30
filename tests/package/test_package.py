from __future__ import annotations

import json
import os
from pathlib import Path
from fractions import Fraction
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from engine.dubflow.package import job, render
from engine.dubflow.product import script
from engine.dubflow.product.job import probe_duration_ms, run_ffmpeg

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = (os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")
           or str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix))) if FFMPEG else None


class FakeSpeaker:
    sample_rate = 48000

    def __init__(self):
        self.closed = False

    def speak(self, text):
        return [0.1] * 12000

    def close(self):
        self.closed = True


class PackageTests(unittest.TestCase):
    def spec(self, root, **changes):
        video = root / "final_vi.mp4"
        if not video.exists():
            video.write_bytes(b"original")
        value = dict(schema_version=1, video=str(video), intro="Xin chào.", outro="Hẹn gặp lại.", voice_id="vi-test",
                     speed=1.0, music=None, music_volume=0.2, background="keep")
        value.update(changes)
        path = root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return job.load_spec(path)

    def stems(self, root):
        (root / "editable").mkdir(exist_ok=True)
        for name in ("dialogue_stem.wav", "background_stem.wav"):
            (root / "editable" / name).write_bytes(b"stem")
        music = root / "music.wav"
        music.write_bytes(b"music")
        return music

    def test_spec_validation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.spec(root).speed, 1.0)
            self.assertEqual(len(self.spec(root, intro="đ" * 600).intro), 600)
            for changes in ({"schema_version": 2}, {"schema_version": True}, {"intro": "x" * 601}, {"outro": "x" * 601},
                            {"intro": "\0"}, {"intro": "\x85"}, {"intro": 2}, {"intro": "!!!"}, {"intro": "", "outro": ""},
                            {"video": "final_vi.mp4"}, {"voice_id": ""}, {"speed": True}, {"speed": 0.9},
                            {"speed": 1.4}, {"speed": float("nan")}, {"music_volume": -1}, {"music_volume": True},
                            {"music_volume": float("inf")}, {"background": "invalid"}, {"background": "replace"}):
                with self.subTest(changes=changes), self.assertRaises(job.PackageError):
                    self.spec(root, **changes)
            music = self.stems(root)
            for mode in ("keep", "replace", "duck"):
                self.assertEqual(self.spec(root, intro="", outro="", music=str(music), background=mode).background, mode)
            for path in ("relative.mp3", str(root / "missing.wav"), str(root / "spec.json")):
                with self.assertRaises(job.PackageError):
                    self.spec(root, music=path)

    def test_audio_plans_and_missing_stems(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = self.spec(root).video
            music = self.stems(root)
            self.assertEqual(render.audio_plan(video, music, 0.2, "keep"), (render.AudioInput(video),))
            replaced = render.audio_plan(video, music, 0.2, "replace")
            self.assertEqual([(p.path.name, p.volume, p.loop) for p in replaced],
                             [("dialogue_stem.wav", 1, False), ("music.wav", 0.2, True)])
            duck = render.audio_plan(video, music, 0.3, "duck")
            self.assertEqual((duck[-1].path.name, duck[-1].volume), ("background_stem.wav", 0.25))
            argv = render.main_argv(Path("ffmpeg"), video, duck, render.Video(320, 180, "30000/1001"), 2300, Path("out.mp4"), encoder="mpeg4")
            graph = argv[argv.index("-filter_complex") + 1]
            self.assertIn("amix=inputs=3", graph)
            self.assertIn("volume=0.2500", graph)
            self.assertIn("atrim=duration=2.300", graph)
            self.assertIn("-stream_loop", argv)
            self.assertIn("48000", argv)
            self.assertIn("192k", argv)
            keep = render.main_argv(Path("ffmpeg"), video, render.audio_plan(video, None, 1, "keep"), render.Video(320, 180, "25"), 1000, Path("out"))
            self.assertEqual(keep.count("-i"), 1)
            self.assertIn("[0:a:0]", keep[keep.index("-filter_complex") + 1])
            (root / "editable/background_stem.wav").unlink()
            with self.assertRaisesRegex(ValueError, "nền gốc"):
                render.audio_plan(video, music, 0.2, "duck")
            with self.assertRaises(job.PackageError):
                self.spec(root, music=str(music), background="duck")
            (root / "editable/dialogue_stem.wav").unlink()
            with self.assertRaisesRegex(ValueError, "giọng Việt"):
                render.audio_plan(video, music, 0.2, "replace")

    def test_ass_cards_follow_the_video_geometry_and_escape_overrides(self):
        captions = [script.Caption(300, 1300, r"Xin {\an1} chào!")]
        for width, height in ((1920, 1080), (1080, 1920), (720, 720)):
            ass = render.ass_document(captions, render.Video(width, height, "25/1"))
            self.assertIn(f"PlayResX: {width}", ass)
            self.assertIn(f"PlayResY: {height}", ass)
            self.assertIn("&H00FFFFFF", ass)
            self.assertIn("WrapStyle: 0", ass)
            self.assertIn("0:00:00.30,0:00:01.30", ass)
            self.assertNotIn(r"{\an1}", ass)
            style = next(line for line in ass.splitlines() if line.startswith("Style:"))
            self.assertIn(",-1,0,0,0,100,100", style)  # Bold.
            self.assertEqual(style.split(",")[18], "5")  # Centered.
            self.assertGreater(int(style.split(",")[19]), 0)
        command = render.card_argv(Path("ffmpeg"), Path("a.png"), Path("n.wav"), "intro.ass", render.Video(320, 180, "25"), 1250, Path("o"))
        self.assertIn("subtitles=intro.ass", command[command.index("-vf") + 1])

    def fake_run(self, root, speaker=None, runner=None, probe=None, **changes):
        spec = self.spec(root, **changes)
        voice = speaker or FakeSpeaker()
        def ffmpeg(argv, cwd, cancelled):
            Path(argv[-1]).write_bytes(b"packaged")
        return job.run(spec, speaker=lambda status: voice, probe=probe or (lambda path: 1000 if path == spec.video else 3500),
                       measure=lambda path: render.Video(320, 180, "25"), ffmpeg=Path("ffmpeg"),
                       ffmpeg_runner=runner or ffmpeg, encoder="mpeg4")

    def test_atomic_publication_keeps_originals_and_reports_progress(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            voice = FakeSpeaker()
            result = self.fake_run(root, speaker=voice)
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertTrue(voice.closed)
            self.assertEqual((root / "final_vi.mp4").read_bytes(), b"original")
            self.assertEqual((root / "final_vi_packaged.mp4").read_bytes(), b"packaged")
            work = root / ".dubflow-work/package"
            self.assertEqual(json.loads((work / "status.json").read_text(encoding="utf-8")), result)
            self.assertFalse((work / "final_vi_packaged.mp4.partial").exists())
            self.assertTrue((work / "intro.receipt.json").is_file())

    def test_cancellation_discards_partial_and_keeps_a_previous_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "final_vi_packaged.mp4"
            previous.write_bytes(b"previous")
            def cancel(argv, cwd, cancelled):
                Path(argv[-1]).write_bytes(b"partial")
                (cwd / "cancel").write_bytes(b"")
                self.assertTrue(cancelled())
            result = self.fake_run(root, runner=cancel)
            self.assertEqual(result["state"], "CANCELLED")
            self.assertEqual(previous.read_bytes(), b"previous")
            self.assertFalse(list((root / ".dubflow-work/package").glob("*.partial")))
            def forbidden(_status):
                raise AssertionError("cancelled before starting speech")
            spec = self.spec(root)
            self.assertEqual(job.run(spec, speaker=forbidden, probe=lambda path: 1000, measure=lambda path: render.Video(320, 180, "25"),
                                    ffmpeg=Path("ffmpeg"))["state"], "CANCELLED")

    def test_failed_validation_preserves_the_previous_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "final_vi_packaged.mp4").write_bytes(b"previous")
            result = self.fake_run(root, probe=lambda path: 1000)
            self.assertEqual(result["state"], "FAILED")
            self.assertEqual((root / "final_vi_packaged.mp4").read_bytes(), b"previous")
            self.assertFalse(list((root / ".dubflow-work/package").glob("*.partial")))

    def test_music_only_never_loads_tts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            music = self.stems(root)
            class NoSpeech(FakeSpeaker):
                def speak(self, text):
                    raise AssertionError("music-only cannot speak")
            voice = NoSpeech()
            result = self.fake_run(root, speaker=voice, intro="", outro="", music=str(music), background="replace", probe=lambda path: 1000)
            self.assertEqual(result["state"], "COMPLETED")
            self.assertFalse(voice.closed)

    def test_verified_parts_resume_and_changed_or_corrupt_inputs_invalidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def runner(argv, cwd, cancelled):
                calls.append(Path(argv[-1]).name)
                Path(argv[-1]).write_bytes(b"rendered")
            self.assertEqual(self.fake_run(root, runner=runner)["state"], "COMPLETED")
            self.assertEqual(len(calls), 6)
            calls.clear()
            self.assertEqual(self.fake_run(root, runner=runner)["state"], "COMPLETED")
            self.assertEqual(calls, ["final_vi_packaged.mp4.partial"])
            (root / ".dubflow-work/package/intro.mp4").write_bytes(b"corrupt")
            calls.clear()
            self.assertEqual(self.fake_run(root, runner=runner)["state"], "COMPLETED")
            self.assertEqual(len(calls), 3, "only the corrupt intro and final assembly rerun")
            (root / "final_vi.mp4").write_bytes(b"changed video")
            calls.clear()
            self.assertEqual(self.fake_run(root, runner=runner)["state"], "COMPLETED")
            self.assertEqual(len(calls), 6)

    def test_cancel_during_final_assembly_preserves_existing_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "final_vi_packaged.mp4").write_bytes(b"previous")
            def runner(argv, cwd, cancelled):
                output = Path(argv[-1])
                output.write_bytes(b"partial")
                if output.name == "final_vi_packaged.mp4.partial":
                    (cwd / "cancel").touch()
            result = self.fake_run(root, runner=runner)
            self.assertEqual(result["state"], "CANCELLED")
            self.assertEqual((root / "final_vi_packaged.mp4").read_bytes(), b"previous")
            self.assertFalse(list((root / ".dubflow-work/package").glob("*.partial*")))

    def test_cli_reports_validation_failure_as_last_ascii_json_line(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            args = [sys.executable, "-B", "-m", "engine.dubflow.package"]
            for name in ("spec", "output-dir", "app-root", "model-root", "ffmpeg", "ffprobe"):
                args += [f"--{name}", str(root / "missing.json" if name == "spec" else root)]
            result = subprocess.run(args, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout.decode("ascii").splitlines()[-1])["state"], "FAILED")
            self.assertEqual(json.loads((root / ".dubflow-work/package/status.json").read_text(encoding="utf-8"))["state"], "FAILED")

    def test_cancel_kills_the_running_subprocess(self):
        with TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaises(job.Cancelled):
                run_ffmpeg([sys.executable, "-B", "-c", "import time; time.sleep(60)"], Path(directory),
                           lambda: time.monotonic() - started > 0.2, poll_seconds=0.05)
            self.assertLess(time.monotonic() - started, 10)


@unittest.skipUnless(FFMPEG and FFPROBE and Path(FFPROBE).is_file(), "FFmpeg/ffprobe are not available")
class FfmpegTests(unittest.TestCase):
    spec = PackageTests.spec
    def test_real_packaging_preserves_size_duration_and_audio_modes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def ff(*args):
                subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", *args], check=True, capture_output=True)
            ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=1.2", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.2",
               "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(root / "final_vi.mp4"))
            (root / "editable").mkdir()
            for name, frequency, duration in (("editable/dialogue_stem.wav", 440, 1.2), ("editable/background_stem.wav", 330, 1.2), ("music.wav", 660, 0.3)):
                ff("-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}", "-ar", "48000", str(root / name))
            original = (root / "final_vi.mp4").read_bytes()
            for mode in ("keep", "replace", "duck"):
                with self.subTest(mode=mode):
                    spec = self.spec(root, background=mode, music=str(root / "music.wav"))
                    result = job.run(spec, speaker=lambda status: FakeSpeaker(), probe=lambda path: probe_duration_ms(Path(FFPROBE), path),
                                     measure=lambda path: job.probe_video(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4")
                    self.assertEqual(result["state"], "COMPLETED", result)
                    output = root / "final_vi_packaged.mp4"
                    self.assertTrue(output.is_file())
                    self.assertAlmostEqual(probe_duration_ms(Path(FFPROBE), output), 3700, delta=200)
                    measured = job.probe_video(Path(FFPROBE), output)
                    self.assertEqual((measured.width, measured.height), (320, 180))
                    self.assertEqual(Fraction(measured.fps), 25)
                    streams = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(output)], capture_output=True, check=True)
                    audio = next(s for s in json.loads(streams.stdout)["streams"] if s["codec_type"] == "audio")
                    self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))
                    self.assertEqual((root / "final_vi.mp4").read_bytes(), original)

    def test_short_portrait_and_square_clips_with_one_card(self):
        with TemporaryDirectory() as directory:
            for width, height, fps, intro, outro in ((180, 320, "30000/1001", "Xin chào.", ""), (180, 180, "24", "", "Hẹn gặp lại.")):
                with self.subTest(size=(width, height)):
                    root = Path(directory) / f"{width}x{height}"
                    root.mkdir()
                    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}:d=0.4",
                                    "-f", "lavfi", "-i", "sine=frequency=220:duration=0.4", "-c:v", "mpeg4", "-c:a", "aac", "-shortest",
                                    str(root / "final_vi.mp4")], check=True, capture_output=True)
                    spec = self.spec(root, intro=intro, outro=outro)
                    result = job.run(spec, speaker=lambda status: FakeSpeaker(), probe=lambda path: probe_duration_ms(Path(FFPROBE), path),
                                     measure=lambda path: job.probe_video(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4")
                    self.assertEqual(result["state"], "COMPLETED", result)
                    measured = job.probe_video(Path(FFPROBE), root / "final_vi_packaged.mp4")
                    self.assertEqual((measured.width, measured.height, Fraction(measured.fps)), (width, height, Fraction(fps)))


if __name__ == "__main__":
    unittest.main()

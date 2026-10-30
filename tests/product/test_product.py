"""Product videos: script splitting, captions, the template, segments and the job."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.product import job, layout, render, script

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")


class ScriptTests(unittest.TestCase):
    def test_sentences_split_at_punctuation_and_bound_their_length(self):
        text = "Chào cả nhà!  Hôm nay mình giới thiệu nồi chiên.\n\n…\n" + "Dùng rất tiện, " * 20 + "giá tốt."
        spoken = script.sentences(text)
        self.assertEqual(spoken[:2], ["Chào cả nhà!", "Hôm nay mình giới thiệu nồi chiên."])
        self.assertTrue(all(0 < len(item) <= script.MAX_SPOKEN_CHARS for item in spoken))
        self.assertEqual(" ".join(spoken[2:]).split(), ("Dùng rất tiện, " * 20 + "giá tốt.").split())

    def test_a_long_word_run_is_wrapped_between_words(self):
        words = "một " * 80
        spoken = script.sentences(words)
        self.assertTrue(all(len(item) <= script.MAX_SPOKEN_CHARS for item in spoken))
        self.assertEqual(" ".join(spoken).split(), words.split())

    def test_captions_follow_speech_and_share_long_sentences_by_length(self):
        long = "Nồi chiên không dầu dung tích lớn, nấu được cho cả gia đình bốn người ăn"
        captions, total = script.captions([("Xin chào.", 1000), (long, 3000)])
        self.assertEqual(captions[0], script.Caption(script.LEAD_MS, script.LEAD_MS + 1000, "Xin chào."))
        second = script.LEAD_MS + 1000 + script.GAP_MS
        rest = captions[1:]
        self.assertGreater(len(rest), 1)
        self.assertTrue(all(len(item.text) <= script.MAX_CAPTION_CHARS for item in rest))
        self.assertEqual((rest[0].start_ms, rest[-1].end_ms), (second, second + 3000))
        self.assertTrue(all(a.end_ms == b.start_ms for a, b in zip(rest, rest[1:])))
        self.assertEqual(total, second + 3000 + script.TAIL_MS)


class LayoutTests(unittest.TestCase):
    def test_template_places_name_price_and_captions_without_user_overrides(self):
        captions = [script.Caption(300, 1500, "Giá chỉ {\\b1}hôm nay")]
        document = layout.ass_document("Nồi chiên {X}", "299.000đ", captions, 2500)
        self.assertIn("PlayResX: 1080\nPlayResY: 1920", document)
        self.assertIn("Title,,0,0,0,,{\\an8\\pos(540,1244)}Nồi chiên (X)", document)
        self.assertIn("Price,,0,0,0,,{\\an2\\pos(540,1850)}299.000đ", document)
        self.assertIn("Dialogue: 1,0:00:00.30,0:00:01.50,Caption,,0,0,0,,{\\an5\\pos(540,1560)}Giá chỉ (/b1)hôm nay", document)

    def test_a_custom_background_outlines_the_name_and_captions(self):
        plain = layout.ass_document("Nồi", "", [], 1000)
        outlined = layout.ass_document("Nồi", "", [], 1000, outlined=True)
        self.assertIn("Style: Caption,Arial,66,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,0,3,5,", plain)
        self.assertIn("Style: Caption,Arial,66,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,5,", outlined)
        self.assertIn("Style: Title,Arial,60,&H0000D4FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,2,8,", outlined)

    def test_empty_name_and_price_are_left_out(self):
        document = layout.ass_document(" ", "", [], 1000)
        self.assertNotIn(",Title,", document)
        self.assertNotIn(",Price,", document)

    def test_srt_numbers_and_times_each_caption(self):
        text = layout.srt_document([script.Caption(0, 1234, "a"), script.Caption(1234, 61000, "b")])
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:01,234\na\n\n2\n00:00:01,234 --> 00:01:01,000\nb\n")


class RenderTests(unittest.TestCase):
    def test_plan_cycles_visuals_in_order_and_continues_clips(self):
        photo, clip = render.Visual(Path("C:/a.jpg")), render.Visual(Path("C:/b.mp4"), 7000)
        segments = render.plan([photo, clip], 21000)
        self.assertEqual(len(segments), math.ceil(21000 / render.MAX_SEGMENT_MS))
        self.assertEqual([item.visual for item in segments], [photo, clip, photo, clip, photo])
        self.assertEqual(sum(item.duration_ms for item in segments), 21000)
        self.assertTrue(all(item.duration_ms <= render.MAX_SEGMENT_MS for item in segments))
        clips = [item for item in segments if item.visual is clip]
        self.assertEqual([item.offset_ms for item in clips], [0, 0])  # 4200 + 4200 passes the clip's end
        self.assertEqual(render.plan([render.Visual(Path("C:/c.mp4"), 10000)], 9000)[1].offset_ms, 4500)

    def test_every_visual_is_shown_even_when_the_video_is_short(self):
        visuals = [render.Visual(Path(f"C:/{index}.png")) for index in range(4)]
        self.assertEqual(len(render.plan(visuals, 3000)), 4)

    def test_command_maps_video_and_mixed_audio(self):
        segments = render.plan([render.Visual(Path("C:/a.jpg")), render.Visual(Path("C:/b.mp4"), 9000)], 8000)
        argv = render.ffmpeg_argv(Path("ffmpeg.exe"), segments, Path("n.wav"), Path("m.mp3"), 0.2, 8000, Path("out.mp4"))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("concat=n=2:v=1:a=0[media]", graph)
        self.assertIn("subtitles=captions.ass[video]", graph)
        self.assertIn("volume=0.20", graph)
        self.assertIn("amix=inputs=2:duration=first", graph)
        self.assertEqual(argv[argv.index("-t", argv.index("-filter_complex")) + 1], "8.000")
        self.assertEqual(argv[-1], "out.mp4")

    def test_tall_backgrounds_fill_the_frame_and_others_the_panel(self):
        for size, full in (((1080, 1920), True), ((1080, 720), False), ((1000, 1000), False), ((1200, 1600), True), ((720, 1280), True)):
            with self.subTest(size=size):
                self.assertEqual(render.background(Path("C:/bg.png"), *size).full_frame, full)
        with self.assertRaises(ValueError):
            render.background(Path("C:/bg.png"), 0, 10)

    def test_background_sits_under_the_media_after_the_audio_inputs(self):
        segments = render.plan([render.Visual(Path("C:/a.jpg"))], 4000)
        for backdrop, scale, place in ((render.Background(Path("C:/bg.png"), False), "scale=1080:720:", "overlay=0:1200[base]"),
                                       (render.Background(Path("C:/bg.png"), True), "scale=1080:1920:", "overlay=0:0[base]")):
            with self.subTest(full=backdrop.full_frame):
                argv = render.ffmpeg_argv(Path("ffmpeg.exe"), segments, Path("n.wav"), Path("m.mp3"), 0.2, 4000, Path("out.mp4"), backdrop=backdrop)
                graph = argv[argv.index("-filter_complex") + 1]
                inputs = [argv[index + 1] for index, value in enumerate(argv) if value == "-i"]
                self.assertEqual(inputs, [str(Path("C:/a.jpg")), "n.wav", "m.mp3", str(Path("C:/bg.png"))])
                self.assertIn(f"[3:v]{scale}", graph)
                self.assertIn(place, graph)
                self.assertIn("[base][media]overlay=0:0", graph)
        plain = render.ffmpeg_argv(Path("ffmpeg.exe"), segments, Path("n.wav"), None, 0.2, 4000, Path("out.mp4"))
        self.assertIn("[panel]null[base]", plain[plain.index("-filter_complex") + 1])

    def test_narration_holds_silence_and_speech(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "n.wav"
            render.write_narration(path, [[0.5] * 480, [-2.0] * 960], 48000, lead_ms=10, gap_ms=20, tail_ms=30)
            with wave.open(str(path)) as reader:
                self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()), (1, 2, 48000))
                self.assertEqual(reader.getnframes(), 480 + 480 + 960 + 960 + 1440)
            self.assertFalse(list(Path(directory).glob("*.partial")))


class FakeSpeaker:
    sample_rate = 48000

    def __init__(self, refuse: str = "") -> None:
        self.spoken: list[str] = []
        self.closed = False
        self.refuse = refuse

    def speak(self, text):
        if self.refuse and self.refuse in text:
            raise RuntimeError("TTS_TEXT_UNSUPPORTED")
        self.spoken.append(text)
        return [0.1] * (480 * len(text))

    def close(self):
        self.closed = True


class JobTests(unittest.TestCase):
    def write_spec(self, root: Path, **changes) -> Path:
        image = root / "a.jpg"
        image.write_bytes(b"jpg")
        value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                 "variants": ["Xin chào. Nồi chiên rất tốt.", "Xin chào. Mua ngay hôm nay."], "visuals": [str(image)]}
        value.update(changes)
        path = root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_spec_rejects_relative_or_unknown_files_and_empty_scripts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(job.load_spec(self.write_spec(root)).name, "Nồi chiên")
            for changes in ({"visuals": ["a.jpg"]}, {"visuals": [str(root / "spec.json")]}, {"variants": ["...", "a"]},
                            {"variants": []}, {"music_volume": 2}, {"voice_id": ""}):
                with self.subTest(changes=changes), self.assertRaises(job.ProductError):
                    job.load_spec(self.write_spec(root, **changes))

    def test_each_variant_renders_and_shared_sentences_are_spoken_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = job.load_spec(self.write_spec(root))
            speaker, commands = FakeSpeaker(), []

            def ffmpeg(argv, cwd, cancelled):
                commands.append((argv, cwd))
                Path(argv[-1]).write_bytes(b"mp4")
            output = root / "out"
            result = job.run(spec, output, speaker=lambda status: speaker, probe=lambda path: 1000, ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg)
            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual(speaker.spoken.count("Xin chào."), 1)
            self.assertTrue(speaker.closed)
            self.assertEqual([video["ok"] for video in result["videos"]], [True, True])
            for number in (1, 2):
                self.assertTrue((output / f"video-{number}.mp4").is_file())
                self.assertTrue((output / f"script-{number}.txt").is_file())
                self.assertIn("Xin chào.", (output / f"captions-{number}.srt").read_text(encoding="utf-8"))
            self.assertEqual(commands[0][1], output / ".dubflow-work")
            status = json.loads((output / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual((status["state"], status["done"], status["total"]), ("COMPLETED", 2, 2))

    def test_a_background_is_measured_once_and_used_for_every_video(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            background = root / "nen.png"
            background.write_bytes(b"png")
            spec = job.load_spec(self.write_spec(root, background=str(background)))
            measured, commands = [], []

            def ffmpeg(argv, cwd, cancelled):
                commands.append(argv)
                Path(argv[-1]).write_bytes(b"mp4")
            result = job.run(spec, root / "out", speaker=lambda status: FakeSpeaker(), probe=lambda path: 1000,
                             measure=lambda path: measured.append(path) or (1080, 720), ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg)
            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual(measured, [background])
            self.assertTrue(all(str(background) in argv for argv in commands))
            self.assertIn(",1,4,2,5,", (root / "out" / ".dubflow-work" / "captions.ass").read_text(encoding="utf-8"))
            with self.assertRaises(job.ProductError):
                job.load_spec(self.write_spec(root, background=str(root / "spec.json")))

    def test_an_unreadable_sentence_fails_only_its_variant(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = job.load_spec(self.write_spec(root))

            def ffmpeg(argv, cwd, cancelled):
                Path(argv[-1]).write_bytes(b"mp4")
            result = job.run(spec, root / "out", speaker=lambda status: FakeSpeaker(refuse="Mua"), probe=lambda path: 1000,
                             ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg)
            self.assertEqual(result["state"], "COMPLETED")
            self.assertEqual(result["message"], "Đã tạo 1/2 video")
            self.assertFalse(result["videos"][1]["ok"])
            self.assertIn("Mua ngay hôm nay.", result["videos"][1]["message"])

    def test_a_voice_that_cannot_start_fails_the_batch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = job.load_spec(self.write_spec(root))

            def broken(status):
                raise job.ProductError("Không khởi động được giọng đọc: TTS_RUNTIME_MISSING")
            result = job.run(spec, root / "out", speaker=broken, probe=lambda path: 1000, ffmpeg=Path("ffmpeg"))
            self.assertEqual((result["state"], result["message"]), ("FAILED", "Không khởi động được giọng đọc: TTS_RUNTIME_MISSING"))


class CancelTests(unittest.TestCase):
    def test_cancel_keeps_finished_videos_and_stops_the_rest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = job.load_spec(JobTests.write_spec(self, root))
            output = root / "out"
            speaker, renders = FakeSpeaker(), []

            def ffmpeg(argv, cwd, cancelled):
                renders.append(argv[-1])
                Path(argv[-1]).write_bytes(b"mp4")
                (cwd / job.CANCEL_FILE).write_bytes(b"")  # The user presses "Hủy" during the first render.
            result = job.run(spec, output, speaker=lambda status: speaker, probe=lambda path: 1000, ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg)
            self.assertEqual((result["state"], result["message"]), ("CANCELLED", "Đã hủy."))
            self.assertEqual(len(renders), 1)
            self.assertFalse((output / "video-1.mp4").exists(), "a render finished after the request is discarded")
            self.assertFalse(list((output / ".dubflow-work").glob("*.partial.mp4")))
            self.assertTrue(speaker.closed)

    def test_cancel_between_videos_keeps_the_first(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = job.load_spec(JobTests.write_spec(self, root))
            output = root / "out"

            def ffmpeg(argv, cwd, cancelled):
                Path(argv[-1]).write_bytes(b"mp4")

            class CancelAfterFirst(FakeSpeaker):
                def speak(self, text):
                    if text == "Mua ngay hôm nay.":
                        (output / ".dubflow-work" / job.CANCEL_FILE).write_bytes(b"")
                    return super().speak(text)
            result = job.run(spec, output, speaker=lambda status: CancelAfterFirst(), probe=lambda path: 1000, ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg)
            self.assertEqual((result["state"], result["message"]), ("CANCELLED", "Đã hủy. Giữ lại 1 video đã xong."))
            self.assertTrue((output / "video-1.mp4").is_file())
            self.assertFalse((output / "video-2.mp4").exists())
            status = json.loads((output / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "CANCELLED")

    def test_a_running_render_is_ended_when_cancelled(self):
        with TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaises(job.Cancelled):
                job.run_ffmpeg([sys.executable, "-c", "import time; time.sleep(60)"], Path(directory),
                               lambda: time.monotonic() - started > 0.5, poll_seconds=0.1)
            self.assertLess(time.monotonic() - started, 20)

    def test_a_failed_render_reports_ffmpeg_errors(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(job.ProductError, "bad input"):
                job.run_ffmpeg([sys.executable, "-c", "import sys; sys.stderr.write('bad input'); sys.exit(1)"], Path(directory))


@unittest.skipUnless(FFMPEG, "FFmpeg is not available")
class FfmpegRenderTests(unittest.TestCase):
    def test_a_photo_and_a_clip_render_to_a_vertical_video_of_the_narration_length(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=640x480:d=1", "-frames:v", "1", str(root / "a.png")], check=True)
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=3:r=25", str(root / "b.mp4")], check=True)
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps({"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                                             "variants": ["Xin chào cả nhà. Hôm nay mình giới thiệu nồi chiên."],
                                             "visuals": [str(root / "a.png"), str(root / "b.mp4")]}), encoding="utf-8")
            output = root / "DubFlow Output" / "run"
            result = job.run(job.load_spec(spec_path), output, speaker=lambda status: FakeSpeaker(), probe=lambda path: 3000,
                             ffmpeg=Path(FFMPEG), encoder="mpeg4")
            self.assertEqual(result["state"], "COMPLETED", result)
            probe = subprocess.run([FFMPEG, "-i", str(output / "video-1.mp4")], capture_output=True, text=True)
            self.assertIn("1080x1920", probe.stderr)
            self.assertIn("Audio:", probe.stderr)

    def pixel(self, video: Path, x: int, y: int) -> tuple[int, int, int]:
        raw = subprocess.run([FFMPEG, "-v", "error", "-ss", "0.5", "-i", str(video), "-frames:v", "1", "-vf", f"crop=2:2:{x}:{y}",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return tuple(raw[:3])

    def test_both_background_sizes_land_where_they_belong(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=640x480:d=1", "-frames:v", "1", str(root / "a.png")], check=True)
            # Panel size: plain red. Frame size: green with a blue band in its last 200 rows,
            # which only shows at the bottom if the image covers the whole frame.
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=1080x720:d=1", "-frames:v", "1", str(root / "panel.png")], check=True)
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=1080x1920:d=1", "-frames:v", "1",
                            "-vf", "drawbox=x=0:y=1720:w=1080:h=200:color=blue:t=fill", str(root / "frame.png")], check=True)
            for name, size, expect in (("panel.png", (1080, 720), lambda r, g, b: r > 180 and g < 80 and b < 80),
                                       ("frame.png", (1080, 1920), lambda r, g, b: b > 180 and r < 80 and g < 80)):
                with self.subTest(background=name):
                    spec_path = root / "spec.json"
                    spec_path.write_text(json.dumps({"schema_version": 1, "name": "", "price": "", "voice_id": "vi-test", "variants": ["Xin chào."],
                                                     "visuals": [str(root / "a.png")], "background": str(root / name)}), encoding="utf-8")
                    output = root / "DubFlow Output" / name
                    result = job.run(job.load_spec(spec_path), output, speaker=lambda status: FakeSpeaker(), probe=lambda path: 1000,
                                     measure=lambda path, size=size: size, ffmpeg=Path(FFMPEG), encoder="mpeg4")
                    self.assertEqual(result["state"], "COMPLETED", result)
                    colour = self.pixel(output / "video-1.mp4", 20, 1890)
                    self.assertTrue(expect(*colour), colour)
                    top = self.pixel(output / "video-1.mp4", 20, 300)
                    self.assertTrue(min(top) > 180, f"the media stays on top: {top}")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.docu import matcher, roughcut, script
from engine.dubflow.docu.project import load_spec, spec_path, write_json
from engine.dubflow.product.job import ProductError

from test_docu import make_run

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class FilterTests(unittest.TestCase):
    def test_frame_counts_sum_to_the_film(self):
        counts = roughcut.frame_counts([0.0, 4.1, 8.2, 12.31], 16.43)
        self.assertEqual(sum(counts), round(16.43 * roughcut.FPS))
        self.assertEqual(counts[0], 123)

    def test_graph_frames_fills_looks_and_bars(self):
        plain = roughcut.video_filter(1920, 1080, {"zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True}, frames=90, ken_burns=False)
        self.assertIn("crop=1920:1080:0:0", plain)
        self.assertIn(roughcut.LOOK, plain)
        self.assertNotIn("zoompan", plain)
        self.assertNotIn("drawbox", plain)
        framed = roughcut.video_filter(1920, 1080, {"zoom": 1.14, "shift": 0.06, "letterbox": 0.1217, "clear": True}, frames=90, ken_burns=True)
        self.assertIn("crop=1684:948:118:1", framed)
        self.assertIn("zoompan=z='1+0.04*on/89'", framed)
        self.assertIn("drawbox=x=0:y=0:w=iw:h=132", framed)
        tall = roughcut.video_filter(1080, 1920, {"zoom": 1.0, "shift": 0.0, "letterbox": 0.0}, frames=90, ken_burns=False)
        self.assertIn("gblur", tall, "a vertical source is fitted over a blurred fill")
        covered = roughcut.video_filter(1920, 1080, {"zoom": 1.14, "shift": 0.06, "letterbox": 0.0, "clear": False,
                                                     "band": {"top": 0.8, "bottom": 0.95, "share": 1.0}}, frames=90, ken_burns=False)
        self.assertIn("crop=iw:2:0:862,scale=iw:162", covered, "the caption band is covered by stretched edge rows")
        self.assertNotIn("split=2[base]", roughcut.video_filter(1920, 1080, {"clear": True, "band": {"top": 0.8, "bottom": 0.95}}, frames=9, ken_burns=False))

    def test_description_and_credits(self):
        with TemporaryDirectory() as temp:
            spec = load_spec(spec_path(make_run(Path(temp))))
            timeline = {"chapters": [{"chapter_type": "HOOK", "start": 0.3}, {"chapter_type": "PROBLEM", "start": 61.0},
                                     {"chapter_type": "CRITICAL_EVALUATION", "start": 3725.0}]}
            text = roughcut.description({"project_title": "How a Dam Defies Physics"}, timeline, spec)
            self.assertEqual(text.splitlines()[:5], ["How a Dam Defies Physics", "", "0:00 The Hook", "1:01 The Problem", "1:02:05 The Verdict"])
            self.assertIn("- Kênh A (Bilibili) – https://www.bilibili.com/video/BV1", text)
            self.assertIn("- Nguồn B", text)
            self.assertIn("synthetic voice", text)
            card = roughcut.end_card(spec)
            self.assertIn("Footage", card)
            self.assertIn("Kênh A (Bilibili) – https://www.bilibili.com/video/BV1\\NNguồn B", card)


class MatcherInterleaveTests(unittest.TestCase):
    def test_uncited_footage_alternates_sources(self):
        sources = [matcher.Source("src_01", 100.0, tuple(float(t) for t in range(4, 100, 4))),
                   matcher.Source("src_02", 100.0, tuple(float(t) for t in range(4, 100, 4)))]
        scene = matcher.Scene("sc_001", 0.0, 24.0, (matcher.Ref("src_01", 40, 44),))
        pieces = matcher.plan([scene], sources)[0][1]
        uncited = [piece for piece in pieces if piece.tier >= matcher.SAME_SOURCES]
        self.assertTrue(uncited)
        for previous, piece in zip(pieces, pieces[1:]):
            if piece.tier >= matcher.SAME_SOURCES:
                self.assertNotEqual(piece.source_id, previous.source_id)


def _project(base: Path, *, card: bool = False) -> tuple[Path, object, dict]:
    run = make_run(base)
    value = json.loads(spec_path(run).read_text(encoding="utf-8"))
    value["burn_end_credits"] = card
    write_json(spec_path(run), value)
    spec = load_spec(spec_path(run))
    draft = {"chapters": [{"chapter_type": "HOOK", "scenes": [{"scene_id": "sc_001", "narration_text": "The dam rises.", "matched_source_refs": []}]}]}
    from engine.dubflow.docu import context
    context.build_all(run, spec, lambda path: 20_000)
    checked = script.check(draft, spec, script.load_contexts(run, spec))
    write_json(run / "documentary_script.json", checked)
    write_json(run / "master_audio_timeline.json", {"script_sha256": checked["script_sha256"], "duration": 6.0,
                                                    "chapters": [{"chapter_type": "HOOK", "start": 0.0}], "scenes": []})
    timeline_hash = roughcut._sha256(run / "master_audio_timeline.json")
    plan = {"script_sha256": checked["script_sha256"], "timeline_sha256": timeline_hash, "duration": 6.0,
            "sources": [{"id": "src_01", "framing": {"zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True}},
                        {"id": "src_02", "framing": {"zoom": 1.08, "shift": 0.0, "letterbox": 0.0, "clear": True}}],
            "scenes": [{"scene_id": "sc_001", "slices": [{"source_id": "src_01", "start": 1.0, "end": 4.0, "at": 0.0},
                                                         {"source_id": "src_02", "start": 2.0, "end": 5.0, "at": 3.0}]}]}
    write_json(run / "visual_plan.json", plan)
    with wave.open(str(run / "narration.wav"), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 48000 * 6)
    return run, spec, plan


class RunTests(unittest.TestCase):
    def test_render_with_receipts_fallback_and_freshness(self):
        with TemporaryDirectory() as temp:
            run, spec, plan = _project(Path(temp), card=True)
            calls = []

            def runner(argv, cwd, cancelled):
                calls.append(argv)
                if "h264_mf" in argv:
                    raise ProductError("Render video thất bại: no Media Foundation")
                Path(cwd, Path(argv[-1]).name).write_bytes(b"mp4")
            result = roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertTrue((run / "documentary.mp4").is_file())
            self.assertEqual((run / ".dubflow-work/render/concat.txt").read_text(encoding="utf-8"),
                             "file 'slice-0001.mp4'\nfile 'slice-0002.mp4'\nfile 'card.mp4'\n")
            self.assertEqual(result["duration"], 6.0 + roughcut.END_CARD)
            self.assertEqual(json.loads((run / "source_credit.json").read_text(encoding="utf-8"))["sources"][0]["source_id"], "src_01")
            self.assertIn("0:00 The Hook", (run / "description.txt").read_text(encoding="utf-8"))
            frames = [argv[argv.index("-frames:v") + 1] for argv in calls if "-frames:v" in argv and "libopenh264" in argv]
            self.assertEqual(frames[:2], ["90", "90"])
            # Again: every slice is reused; only the card and the mux run.
            calls.clear()
            roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertFalse(any(any(str(arg).startswith("slice-") for arg in argv) for argv in calls if "-ss" in argv))
            # A plan older than the narration is refused.
            write_json(run / "master_audio_timeline.json", {"script_sha256": "x", "duration": 7.0, "scenes": []})
            stale = roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertEqual(stale["state"], "FAILED")
            self.assertIn("cũ", stale["message"])

    @unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
    def test_real_render_is_frame_exact(self):
        with TemporaryDirectory() as temp:
            run, spec, plan = _project(Path(temp))
            for name in ("a.mp4", "b.mp4"):
                subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
                                "-pix_fmt", "yuv420p", str(Path(temp) / name)], check=True, capture_output=True)
            result = roughcut.run(run, spec, ffmpeg=Path(FFMPEG), probe=lambda video: roughcut.probe_display(Path(FFPROBE), video),
                                  encoders=("libopenh264", "h264_mf", "libx264", "mpeg4"))
            self.assertEqual(result["state"], "COMPLETED", result)
            probe = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries", "stream=codec_type,width,height,nb_read_frames",
                                               "-of", "json", str(run / "documentary.mp4")], capture_output=True, text=True).stdout)
            video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1920, 1080))
            self.assertEqual(int(video["nb_read_frames"]), 180)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))


if __name__ == "__main__":
    unittest.main()

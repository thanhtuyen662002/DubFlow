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
from engine.dubflow.docu.project import DocuError, load_spec, spec_path, write_json
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
        # Not stretched edge rows any more (#548): the band, from a 38 px feather above it, blurred, darkened and faded in.
        self.assertNotIn("scale=iw:", covered)
        self.assertIn("[band]crop=iw:200:0:826,gblur=sigma=20:steps=3,curves=all='0/0 1/0.75'", covered)
        self.assertIn("gradients=s=1920x200:c0=black:c1=white:x0=0:y0=0:x1=0:y1=38", covered)
        self.assertIn("[soft][mask]alphamerge[strip];[base][strip]overlay=0:826[covered]", covered)
        self.assertNotIn("split=2[base]", roughcut.video_filter(1920, 1080, {"clear": True, "band": {"top": 0.8, "bottom": 0.95}}, frames=9, ken_burns=False))
        # Another channel's logo is blurred where it sits, before the band cover and the framing.
        logo = roughcut.video_filter(1920, 1080, {"clear": False, "band": {"top": 0.8, "bottom": 0.95}, "logos": [[0.043, 0.682, 0.152, 0.118]]},
                                     frames=9, ken_burns=False)
        # The blur grows with the box and fades out over a margin round it instead of ending on a hard edge (#556).
        self.assertIn("[0:v]split=2[logo0base][logo0cut];[logo0cut]crop=474:332:0:632,gblur=sigma=26:steps=3,format=yuva420p[logo0soft];"
                      "color=c=black:s=474x332,drawbox=x=29:y=51:w=394:h=230:color=white:t=fill,gblur=sigma=17:steps=3,format=gray[logo0mask];"
                      "[logo0soft][logo0mask]alphamerge[logo0blur];[logo0base][logo0blur]overlay=0:632:shortest=1[logo0];"
                      "[logo0]split=2[base][band]", logo)
        # Along the frame's own edge the blur stays opaque to the border.
        corner = roughcut.video_filter(1920, 1080, {"clear": True, "logos": [[0.0, 0.0, 0.2854, 0.2367]]}, frames=9, ken_burns=False)
        self.assertIn("crop=752:460:0:0,gblur=sigma=51:steps=3", corner)
        self.assertIn("drawbox=x=0:y=0:w=650:h=358", corner)

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

    def test_number_cards_captions_and_music_are_in_the_rough_cut(self):
        # 2026-11-04 test: the draft had only B-roll and the voice; the script's cards were editor-only.
        from engine.dubflow.docu import overlays
        with TemporaryDirectory() as temp:
            run, spec, plan = _project(Path(temp))
            draft = json.loads((run / "documentary_script.json").read_text(encoding="utf-8"))
            draft["chapters"][0]["scenes"][0]["on_screen_graphic"] = {"type": "METRIC_CALLOUT", "title": "Pigs a year", "value": "1.2 million", "subtext": "two towers"}
            write_json(run / "documentary_script.json", draft)
            timeline = json.loads((run / "master_audio_timeline.json").read_text(encoding="utf-8"))
            timeline["scenes"] = [{"scene_id": "sc_001", "start": 0.0, "end": 5.5, "slot_end": 6.0}]
            write_json(run / "master_audio_timeline.json", timeline)
            plan["timeline_sha256"] = roughcut._sha256(run / "master_audio_timeline.json")
            write_json(run / "visual_plan.json", plan)
            (run / "captions_en.srt").write_text("1\n00:00:00,000 --> 00:00:02,500\nThe dam rises.\n", encoding="utf-8")
            overlays.save_options(run, captions=True)
            calls = []

            def runner(argv, cwd, cancelled):
                calls.append(argv)
                Path(cwd, Path(argv[-1]).name).write_bytes(b"data")
            result = roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertEqual(result["overlays"], {"graphics": True, "captions": True, "music": "generated", "music_path": None, "sfx": True, "mix": "standard"})
            work = run / ".dubflow-work" / "render"
            first, second = (work / "slice-0001.ass").read_text(encoding="utf-8"), (work / "slice-0002.ass").read_text(encoding="utf-8")
            # The hook's first number is a hero callout (#548): it slides in on slice 1 (0.6 s) and fades out on
            # slice 2 at the scene's end, without sliding in again at the cut; the caption is on slice 1 only.
            self.assertIn("Dialogue: 1,0:00:00.60,0:00:03.00,HeroValue,,0,0,0,,{\\move(80,516,170,516,0,350)\\fad(250,0)}1.2 million", first)
            self.assertIn("HeroValue,,0,0,0,,{\\pos(170,516)\\fad(0,250)}1.2 million", second)
            self.assertIn("Accent,,0,0,0,,{\\move(46,466,136,466,0,350)\\fad(250,0)}{\\p1}m 0 0 l 8 0 8 246", first)
            self.assertIn("Caption,,0,0,0,,{\\fad(250,250)}The dam rises.", first)
            self.assertNotIn("The dam rises.", second)
            bed = next(argv for argv in calls if any("aevalsrc" in str(arg) for arg in argv))
            self.assertTrue(str(bed[-1]).endswith("bed.partial.wav"))
            mux = calls[-1]
            self.assertIn("[audio]", mux)
            self.assertIn("sidechaincompress", mux[mux.index("-filter_complex") + 1])
            # Off: no bed, the voice alone; no cards, no .ass for these slices.
            overlays.save_options(run, graphics=False, captions=False, music="none")
            calls.clear()
            roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertFalse(any(any("aevalsrc" in str(arg) for arg in argv) for argv in calls))
            self.assertIn("1:a:0", calls[-1])

    def test_render_options_are_checked(self):
        from engine.dubflow.docu import overlays
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            self.assertEqual(overlays.load_options(run), overlays.DEFAULTS)
            with self.assertRaises(DocuError):
                overlays.save_options(run, music="file")
            with self.assertRaises(DocuError):
                overlays.save_options(run, music_path=str(Path(temp) / "missing.mp3"))
            with self.assertRaises(DocuError):
                overlays.save_options(run, graphics="yes")
            song = Path(temp) / "song.mp3"
            song.write_bytes(b"id3")
            self.assertEqual(overlays.save_options(run, music_path=str(song))["music"], "file")
            argv = overlays.file_bed_argv(Path("ffmpeg"), song, 120.0, Path(temp) / "bed.wav")
            self.assertEqual(argv[argv.index("-stream_loop") + 1], "-1")
            self.assertIn("afade=t=out:st=116.000", argv[argv.index("-filter_complex") + 1])

    def test_mood_beds_follow_the_chapters(self):
        from engine.dubflow.docu import overlays
        script_value = {"chapters": [{"chapter_type": "HOOK", "recommended_bgm_mood": "cinematic_tension_drone"},
                                     {"chapter_type": "PROBLEM", "recommended_bgm_mood": "dark_suspense"}]}
        timeline = {"chapters": [{"chapter_type": "HOOK", "start": 0.4, "slot_end": 20.0}, {"chapter_type": "PROBLEM", "start": 20.0, "slot_end": 60.0}]}
        spans = overlays.chapter_moods(script_value, timeline, 66.0)
        self.assertEqual(spans, [("cinematic_tension_drone", 0.0, 20.0), ("dark_suspense", 20.0, 66.0)])
        argv = overlays.bed_argv(Path("ffmpeg"), spans, 66.0, Path("bed.wav"))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("adelay=20000:all=1", graph)
        self.assertIn("loudnorm=I=-20", graph)
        self.assertNotEqual(overlays.mood_expression("curious_pulse"), overlays.mood_expression("dark_suspense"))
        self.assertEqual(overlays.chapter_moods({"chapters": []}, {"chapters": []}, 10.0), [("reflective_ambient", 0.0, 10.0)])

    def test_a_slice_label_is_drawn_on_that_slice_only(self):
        with TemporaryDirectory() as temp:
            run, spec, plan = _project(Path(temp))
            write_json(run / ".dubflow-work" / roughcut.LABELS, {"schema_version": 1, "labels": [
                {"id": "l001", "scene_id": "sc_001", "text": "Control room", "x": 0.5, "y": 0.8, "w": 0.3, "clip_index": 1},
                {"id": "l002", "scene_id": "sc_001", "text": "Ezhou", "x": 0.5, "y": 0.2, "w": 0.3}]})
            graphs = []

            def runner(argv, cwd, cancelled):
                if "-filter_complex" in argv:
                    graphs.append(argv[argv.index("-filter_complex") + 1])
                Path(cwd, Path(argv[-1]).name).write_bytes(b"mp4")
            self.assertEqual(roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)["state"], "COMPLETED")
            work = run / ".dubflow-work" / "render"
            first, second = (work / "slice-0001.ass").read_text(encoding="utf-8"), (work / "slice-0002.ass").read_text(encoding="utf-8")
            self.assertNotIn("Control room", first)
            self.assertIn("Control room", second)
            self.assertIn("Ezhou", first)
            self.assertIn("Ezhou", second)

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
                                               "-of", "json", str(run / "documentary.mp4")], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)
            video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1920, 1080))
            self.assertEqual(int(video["nb_read_frames"]), 180)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))


if __name__ == "__main__":
    unittest.main()

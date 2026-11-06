from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import matcher, safezone, shots, visual
from engine.dubflow.docu.project import load_spec, spec_path, write_json
from engine.dubflow.media.adapter import BlurRegion

from test_docu import make_run


class ShotTests(unittest.TestCase):
    def test_parse_keeps_real_cuts_in_order(self):
        text = "frame:0 pts:1\nlavfi.scd.score=40\nlavfi.scd.time=0.1\nlavfi.scd.time=2.066688\nlavfi.scd.time=2.2\n" \
               "lavfi.scd.time=5\nlavfi.scd.time=9.9\n"
        self.assertEqual(shots.parse(text, 10.0), [2.067, 5.0])
        self.assertEqual(shots.shots([2.0, 5.0], 8.0), [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0)])

    def test_detect_runs_once_per_file_and_threshold(self):
        with TemporaryDirectory() as temp:
            video = Path(temp) / "a.mp4"
            video.write_bytes(b"video")
            cache = Path(temp) / "src_01" / "shots.json"
            calls = []

            def runner(argv, cwd, cancelled):
                calls.append(argv)
                self.assertRegex(" ".join(argv), r"scale=320:-2,scdet=threshold=[0-9.]+:sc_pass=1,metadata=mode=print:file=scdet\.txt")
                (cwd / "scdet.txt").write_text("lavfi.scd.time=3.5\nlavfi.scd.time=7.25\n", encoding="utf-8")
            first = shots.detect(video, 12.0, cache, ffmpeg=Path("ffmpeg"), run=runner, cancelled=lambda: False)
            again = shots.detect(video, 12.0, cache, ffmpeg=Path("ffmpeg"), run=runner, cancelled=lambda: False)
            self.assertEqual(first, [3.5, 7.25])
            self.assertEqual(again, first)
            self.assertEqual(len(calls), 1)
            shots.detect(video, 12.0, cache, ffmpeg=Path("ffmpeg"), run=runner, cancelled=lambda: False, threshold=20)
            self.assertEqual(len(calls), 2)


class SafeZoneTests(unittest.TestCase):
    def test_band_needs_captions_most_of_the_speech(self):
        rows = [(i / 2, 0.86, 0.92) for i in range(16)]
        band = safezone.band_from_rows(rows, 10.0)
        self.assertEqual((band.top, band.bottom, band.share), (0.86, 0.92, 0.8))
        self.assertIsNone(safezone.band_from_rows(rows[:4], 10.0))
        self.assertIsNone(safezone.band_from_rows([], 10.0))

    def test_title_cards_do_not_pull_the_band_up(self):
        # 2026-11-04 pig-tower run: captions at 0.88–0.95 in most samples, plus a few centred title cards
        # ("120万头猪要怎么养?", "1.4万m²") at 0.45–0.65. The old union of merged regions put the band top at 0.39.
        rows = [(i / 2, 0.88 + (i % 3) * 0.004, 0.945 + (i % 3) * 0.004) for i in range(200)]
        rows += [(100 + i / 2, 0.45, 0.62) for i in range(12)] + [(110 + i / 2, 0.52, 0.66) for i in range(8)]
        band = safezone.band_from_rows(rows, 110.0)
        self.assertGreaterEqual(band.top, 0.87)
        self.assertLessEqual(band.bottom, 0.96)
        self.assertTrue(safezone.framing(band, "letterbox_235").clear)

    def test_logos_are_padded_and_scanned_once(self):
        from engine.dubflow.ocr.caption_scan import TextBox

        class Scanner:
            calls = 0

            def scan_frame_text(self, video, duration_s, band_top=None):
                Scanner.calls += 1
                return [TextBox(0.055, 0.694, 0.128, 0.094), TextBox(0.99, 0.0, 0.01, 0.02)], [2.0, 4.0, 9.0]

        with TemporaryDirectory() as temp:
            cache = Path(temp) / "logos.json"
            args = dict(duration=10.0, cache=cache, scanner=Scanner, identity={"recipe": "r"})
            logos = safezone.scan_logos(Path(temp) / "a.mp4", **args)
            self.assertEqual(logos[0], [0.0315, 0.6282, 0.175, 0.2256])
            # The outline triangle round "太空记" (src_02, 2026-11-05) reaches y 0.637 and runs x 0.061-0.180.
            self.assertLessEqual(logos[0][1], 0.637)
            self.assertLessEqual(logos[0][0], 0.061)
            self.assertGreaterEqual(logos[0][0] + logos[0][2], 0.180)
            self.assertEqual(logos[1], [0.978, 0.0, 0.022, 0.034], "kept inside the frame")
            self.assertEqual(safezone.scan_logos(Path(temp) / "a.mp4", **args), logos)
            self.assertEqual(Scanner.calls, 1)

    def test_two_caption_lines_make_one_band(self):
        rows = [(i / 2, 0.80, 0.85) for i in range(40)] + [(i / 2, 0.86, 0.91) for i in range(40)]
        band = safezone.band_from_rows(rows, 20.0)
        self.assertEqual((band.top, band.bottom), (0.8, 0.91))

    def test_scan_band_rescans_old_recipes_and_keeps_the_picture_size(self):
        class Result:
            caption_rows = tuple((i / 2, 0.87, 0.94) for i in range(20))
            frame_size = (360, 640)

            def summary(self):
                return {}

        class Scanner:
            calls = 0

            def scan(self, video, duration_s, speech_ms):
                Scanner.calls += 1
                return Result()

        with TemporaryDirectory() as temp:
            cache = Path(temp) / "captions.json"
            # A scan saved by 1.1.49: same identity, band read from merged regions.
            write_json(cache, {"schema_version": 1, "identity": {"recipe": "r"}, "band": {"top": 0.39, "bottom": 1.0, "share": 1.0}})
            args = dict(duration=10.0, speech=[(0.0, 10.0)], cache=cache, scanner=Scanner, identity={"recipe": "r"})
            band, size = safezone.scan_band(Path(temp) / "a.mp4", **args)
            self.assertEqual((band.top, size), (0.87, (360, 640)))
            self.assertTrue(safezone.is_portrait(size))
            self.assertEqual(safezone.scan_band(Path(temp) / "a.mp4", **args), (band, size))
            self.assertEqual(Scanner.calls, 1)
        self.assertFalse(safezone.is_portrait((1920, 1080)))
        self.assertFalse(safezone.is_portrait(None))

    def test_framing_hides_the_band_within_the_limits(self):
        band = safezone.Band(0.86, 0.93, 0.9)
        plain = safezone.framing(None, "none")
        self.assertEqual((plain.zoom, plain.shift, plain.letterbox, plain.clear), (1.0, 0.0, 0.0, True))
        self.assertEqual(safezone.framing(None, "overscan_108").zoom, 1.08)
        boxed = safezone.framing(None, "letterbox_235")
        self.assertAlmostEqual(boxed.letterbox, 0.1217, places=3)
        for preset in ("none", "letterbox_235", "overscan_108"):
            with self.subTest(preset):
                framed = safezone.framing(band, preset)
                self.assertLessEqual(framed.zoom, safezone.MAX_ZOOM + 1e-9)
                visible = 1 / framed.zoom
                self.assertLessEqual(framed.shift, (1 - visible) / 2 + 1e-9, "no black edge at the top")
                bottom = 0.5 + visible / 2 - framed.shift - visible * framed.letterbox
                if framed.clear:
                    self.assertLessEqual(bottom, band.top)
        # A caption band this high cannot be cleared by 114% alone; the letterbox clears it.
        self.assertFalse(safezone.framing(band, "none").clear)
        self.assertTrue(safezone.framing(band, "letterbox_235").clear)
        self.assertTrue(safezone.framing(safezone.Band(0.93, 0.98, 1.0), "none").clear)


def sources():
    return [matcher.Source("src_01", 120.0, tuple(float(t) for t in range(5, 120, 5))),
            matcher.Source("src_02", 90.0, tuple(t + 0.5 for t in range(3, 90, 3)))]


class WarningTests(unittest.TestCase):
    def test_caption_warning_follows_the_preset(self):
        boxed = visual.caption_warning("src_01", "letterbox_235")
        self.assertIn("làm mờ và phủ dải tối", boxed)
        self.assertNotIn("Chọn khung Letterbox", boxed)
        self.assertIn("Chọn khung Viền đen điện ảnh 2,35:1", visual.caption_warning("src_01", "none"))


class MatcherTests(unittest.TestCase):
    def test_fit_length(self):
        # The whole rest of the slot when the shot holds it; never a leftover shorter than a slice.
        self.assertEqual(matcher.fit_length(4.0, 3.0, 10.0, 1.8, 6.5), 4.0)
        self.assertAlmostEqual(matcher.fit_length(5.0, 4.0, 10.0, 1.8, 6.5), 5.0)
        self.assertAlmostEqual(matcher.fit_length(12.0, 4.4, 10.0, 1.8, 6.5), 4.4)
        self.assertAlmostEqual(matcher.fit_length(6.0, 4.4, 4.4, 1.8, 6.5), 4.2)
        self.assertIsNone(matcher.fit_length(12.0, 4.0, 1.2, 1.8, 6.5))

    def test_rules(self):
        scenes = [matcher.Scene("sc_001", 0, 12.3, (matcher.Ref("src_01", 10, 30), matcher.Ref("src_02", 20, 30))),
                  matcher.Scene("sc_002", 12.3, 31.0, (matcher.Ref("src_01", 40, 44),)),
                  matcher.Scene("sc_003", 31.0, 33.0, (matcher.Ref("src_02", 0, 1),))]
        planned = matcher.plan(scenes, sources())
        slices = [piece for _, pieces, _ in planned for piece in pieces]
        by_id = {source.id: source for source in sources()}
        for scene, pieces, _ in planned:
            self.assertAlmostEqual(sum(p.duration for p in pieces), scene.end - scene.start, places=2)
            self.assertAlmostEqual(pieces[0].at, scene.start, places=3)
        for previous, piece in zip(slices, slices[1:]):
            self.assertAlmostEqual(piece.at, previous.at + previous.duration, places=2)
            if piece.source_id == previous.source_id:
                gap = max(piece.start - previous.end, previous.start - piece.end)
                self.assertGreaterEqual(gap, matcher.SAME_SOURCE_GAP - 1e-6)
        for a in slices:
            for b in slices:
                if a is not b and a.source_id == b.source_id:
                    self.assertFalse(a.start < b.end - 0.05 and a.end > b.start + 0.05, "no range is used twice")
        for piece in slices:
            # One shot, one slice: inside one clean shot window.
            self.assertTrue(any(a - 1e-6 <= piece.start and piece.end <= b + 1e-6 for a, b in by_id[piece.source_id].windows),
                            (piece.source_id, piece.start, piece.end))
            self.assertTrue(matcher.MIN_SLICE - 1e-6 <= piece.duration <= matcher.MAX_SLICE + 1e-6 or piece is slices[-1])
        # The first scene alternates sources and stays in the cited footage.
        first = planned[0][1]
        self.assertEqual([p.source_id for p in first], ["src_01", "src_02", "src_01", "src_02"])
        self.assertEqual({p.tier for p in first}, {matcher.CITED})
        # sc_002 cites 4 s of src_01: it needs more, and says where it came from.
        self.assertTrue(planned[1][2])
        self.assertEqual(matcher.plan(scenes, sources()), planned, "deterministic")

    def test_uncited_filler_comes_from_next_to_the_cited_footage(self):
        # 2026-11-04 test: a scene about pig prices got a disinfection cabinet from the top of another source.
        one = [matcher.Source("src_01", 300.0, tuple(float(t) for t in range(4, 300, 4)))]
        scene = matcher.Scene("sc_001", 0.0, 20.0, (matcher.Ref("src_01", 200, 204),))
        pieces = matcher.plan([scene], one)[0][1]
        self.assertTrue(any(piece.tier >= matcher.SAME_SOURCES for piece in pieces))
        self.assertTrue(all(abs(piece.start - 202) < 40 for piece in pieces), [piece.start for piece in pieces])

    def test_footage_with_big_text_is_left_out(self):
        # Oscar, 2026-11-04: cuts showing big Chinese text should not be used.
        titled = matcher.Source("src_01", 120.0, tuple(float(t) for t in range(5, 120, 5)), text=((8.0, 22.0),))
        scene = matcher.Scene("sc_001", 0, 12.0, (matcher.Ref("src_01", 5, 30),))
        pieces = matcher.plan([scene], [titled])[0][1]
        self.assertTrue(all(piece.end <= 8.0 or piece.start >= 22.0 for piece in pieces), [(p.start, p.end) for p in pieces])
        # Nothing else at all: the text footage still fills the slot rather than failing the plan.
        tiny = matcher.Source("src_01", 6.0, (), text=((0.0, 6.0),))
        self.assertTrue(matcher.plan([matcher.Scene("sc_001", 0, 4.0, ())], [tiny])[0][1])

    def test_a_portrait_source_is_the_last_resort(self):
        phone = matcher.Source("src_03", 120.0, tuple(float(t) for t in range(5, 120, 5)), portrait=True)
        scenes = [matcher.Scene("sc_001", 0, 12.0, (matcher.Ref("src_03", 10, 40), matcher.Ref("src_01", 10, 30)))]
        pieces = matcher.plan(scenes, [*sources(), phone])[0][1]
        self.assertNotIn("src_03", {piece.source_id for piece in pieces})
        # With nothing else left, the phone video still fills the scene.
        only = matcher.plan(scenes, [phone])[0][1]
        self.assertEqual({piece.source_id for piece in only}, {"src_03"})

    def test_reuse_is_the_last_resort(self):
        tiny = [matcher.Source("src_01", 8.0, (4.0,)), matcher.Source("src_02", 8.0, (4.0,))]
        scenes = [matcher.Scene(f"sc_{i:03d}", i * 4.0, (i + 1) * 4.0, (matcher.Ref("src_01", 0, 8),)) for i in range(6)]
        planned = matcher.plan(scenes, tiny)
        self.assertEqual(planned[-1][1][0].tier, matcher.REUSED)
        self.assertIn(matcher.TIER_NOTES[matcher.REUSED], planned[-1][2])
        with self.assertRaises(ValueError):
            matcher.plan([matcher.Scene("sc_001", 0, 20, ())], [matcher.Source("src_01", 2.0, ())])


class VisualRunTests(unittest.TestCase):
    def test_plan_needs_a_fresh_narration_and_writes_the_plan(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            spec = load_spec(spec_path(run))
            from engine.dubflow.docu import context, script
            context.build_all(run, spec, lambda path: 20_000)
            draft = {"chapters": [{"chapter_type": "HOOK", "scenes": [
                {"scene_id": "sc_001", "narration_text": "The dam rises above the canyon.", "matched_source_refs": [{"source_id": "src_01", "start": 0, "end": 9}]},
                {"scene_id": "sc_002", "narration_text": "Turbines spin inside the mountain.", "matched_source_refs": [{"source_id": "src_02", "start": 8, "end": 9}]}]}]}
            checked = script.check(draft, spec, script.load_contexts(run, spec))
            write_json(run / "documentary_script.json", checked)

            def runner(argv, cwd, cancelled):
                (cwd / "scdet.txt").write_text("lavfi.scd.time=4\nlavfi.scd.time=9\nlavfi.scd.time=13\n", encoding="utf-8")
            missing = visual.run(run, spec, ffmpeg=Path("ffmpeg"), scanner=None, ocr_identity=None, ffmpeg_runner=runner)
            self.assertEqual(missing["state"], "FAILED")
            timeline = {"script_sha256": "old", "duration": 9.0, "scenes": [
                {"scene_id": "sc_001", "start": 0.2, "end": 3.8, "slot_end": 4.6}, {"scene_id": "sc_002", "start": 4.6, "end": 8.6, "slot_end": 9.0}]}
            write_json(run / "master_audio_timeline.json", timeline)
            stale = visual.run(run, spec, ffmpeg=Path("ffmpeg"), scanner=None, ocr_identity=None, ffmpeg_runner=runner)
            self.assertEqual(stale["state"], "FAILED")
            self.assertIn("cũ", stale["message"])
            write_json(run / "master_audio_timeline.json", {**timeline, "script_sha256": checked["script_sha256"]})
            done = visual.run(run, spec, ffmpeg=Path("ffmpeg"), scanner=None, ocr_identity=None, ffmpeg_runner=runner)
            self.assertEqual(done["state"], "COMPLETED", done)
            plan = json.loads((run / "visual_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["script_sha256"], checked["script_sha256"])
            self.assertEqual([s["scene_id"] for s in plan["scenes"]], ["sc_001", "sc_002"])
            self.assertEqual(plan["scenes"][0]["start"], 0.0, "the picture starts with the film")
            self.assertEqual(plan["sources"][0]["framing"], {"band": None, "zoom": 1.0, "shift": 0.0, "letterbox": 0.1217, "clear": True,
                                                             "shift_x": 0.0, "frame_size": None, "portrait": False, "logos": [], "text_spans": [],
                                                             "heavy_watermark": False, "usable": True, "logos_cropped": 0, "text_share": 0.0})
            self.assertAlmostEqual(sum(s["duration"] for scene in plan["scenes"] for s in scene["slices"]), 9.0, places=2)


if __name__ == "__main__":
    unittest.main()

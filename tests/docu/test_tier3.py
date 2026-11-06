"""Tier-3 documentary pipeline: one shot per slice, zero-blemish sources, satellite map, data slides, UI sounds."""
from __future__ import annotations

import json
import random
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.docu import infographic, mapgen, matcher, overlays, roughcut, safezone, script, soundscape, visual
from engine.dubflow.docu.project import MAX_SOURCES, MIN_SOURCES, load_spec, spec_path, write_json
from test_docu import make_run

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _inside_one_shot(source: matcher.Source, piece: matcher.Slice) -> bool:
    return any(a - 1e-6 <= piece.start and piece.end <= b + 1e-6 for a, b in source.windows)


class OneShotOneSliceTests(unittest.TestCase):
    def _sources(self, seed: int) -> list[matcher.Source]:
        rng = random.Random(seed)
        found = []
        for number in range(1, 5):
            duration, cuts, t = rng.uniform(80, 240), [], 0.0
            while True:
                t += rng.choice((1.2, 2.2, 3.1, 4.0, 5.6, 6.8, 9.5))
                if t >= duration - 0.5:
                    break
                cuts.append(round(t, 3))
            found.append(matcher.Source(f"src_{number:02d}", round(duration, 3), tuple(cuts)))
        return found

    def test_no_slice_runs_across_a_cut_and_lengths_vary(self):
        for seed in range(6):
            sources = self._sources(seed)
            by_id = {source.id: source for source in sources}
            scenes, at = [], 0.0
            for number in range(1, 30):
                slot = random.Random(seed * 100 + number).uniform(4.0, 16.0)
                kind = "HOOK" if number < 4 else "PROBLEM"
                refs = (matcher.Ref("src_01", number * 3.0, number * 3.0 + 8.0),)
                scenes.append(matcher.Scene(f"sc_{number:03d}", at, at + slot, refs, hold="first" if number == 1 else ""))
                at += slot
            planned = matcher.plan(scenes, sources)
            lengths = []
            for scene, pieces, _ in planned:
                self.assertAlmostEqual(sum(piece.duration for piece in pieces), scene.end - scene.start, places=2)
                for piece in pieces:
                    self.assertFalse(piece.crossed)
                    self.assertTrue(_inside_one_shot(by_id[piece.source_id], piece), (seed, piece))
                    self.assertLessEqual(piece.duration, matcher.MAX_SLICE + 1e-6)
                    lengths.append(round(piece.duration, 1))
            short = [length for length in lengths if length < matcher.MIN_SLICE - 1e-6]
            # Only a scene's last bit may come out shorter, and rarely.
            self.assertLess(len(short), len(lengths) * 0.05, short)
            self.assertGreater(len(set(lengths)), 8, "lengths follow the shots and the rhythm, not one value")

    def test_the_hook_opens_on_a_held_shot(self):
        source = matcher.Source("src_01", 60.0, (3.0, 6.0, 12.5, 15.0, 18.0, 21.0))
        scene = matcher.Scene("sc_001", 0.0, 14.0, (matcher.Ref("src_01", 0.0, 21.0),), hold="first")
        first = matcher.plan([scene], [source])[0][1][0]
        self.assertGreaterEqual(first.duration, matcher.LONG_MIN - 1e-6)
        self.assertEqual(first.start, 6.12)
        other = matcher.Source("src_02", 60.0, (2.5, 5.0, 8.0, 15.0, 30.0))
        closing = matcher.Scene("sc_002", 0.0, 14.0, (matcher.Ref("src_01", 0.0, 21.0),), hold="last")
        last = matcher.plan([closing], [source, other])[0][1][-1]
        self.assertGreaterEqual(last.duration, matcher.LONG_MIN - 1e-6)

    def test_windows_keep_off_the_cuts(self):
        source = matcher.Source("src_01", 20.0, (5.0, 7.2))
        self.assertEqual(source.windows, ((0.0, 4.88), (5.12, 7.08), (7.32, 20.0)))
        # A 2.2 s shot gives a ~2 s slice.
        piece = matcher.plan([matcher.Scene("sc_001", 0.0, 1.96, (matcher.Ref("src_01", 5.0, 7.2),))], [source])[0][1][0]
        self.assertEqual((piece.start, piece.end), (5.12, 7.08))

    def test_a_dense_source_crosses_a_cut_only_as_a_last_resort(self):
        strobe = matcher.Source("src_01", 30.0, tuple(round(0.5 * n, 3) for n in range(1, 60)))
        planned = matcher.plan([matcher.Scene("sc_001", 0.0, 4.0, ())], [strobe])
        self.assertTrue(all(piece.crossed for piece in planned[0][1]))
        self.assertIn(matcher.CROSSED_NOTE, planned[0][2])


class CleanFrameTests(unittest.TestCase):
    def test_text_is_never_used_while_reused_footage_exists(self):
        clean = [matcher.Source("src_01", 10.0, ()), matcher.Source("src_03", 10.0, ())]
        titled = matcher.Source("src_02", 60.0, (), text=((0.0, 60.0),))
        scenes = [matcher.Scene(f"sc_{n:03d}", n * 5.0, (n + 1) * 5.0, ()) for n in range(8)]
        planned = matcher.plan(scenes, [*clean, titled])
        self.assertEqual({piece.source_id for _, pieces, _ in planned for piece in pieces}, {"src_01", "src_03"})

    def test_heavy_watermark_ranks_last_and_unusable_is_skipped(self):
        clean = matcher.Source("src_02", 30.0, (10.0, 20.0))
        heavy = matcher.Source("src_01", 120.0, (), heavy=True)
        scene = matcher.Scene("sc_001", 0.0, 12.0, (matcher.Ref("src_01", 0.0, 60.0),))
        pieces = matcher.plan([scene], [heavy, clean])[0][1]
        self.assertEqual(pieces[0].source_id, "src_02", "clean footage first, even uncited")
        gone = matcher.Source("src_01", 120.0, (), heavy=True, usable=False)
        self.assertNotIn("src_01", {piece.source_id for piece in matcher.plan([scene] * 1, [gone, clean])[0][1]})
        # Nothing but unusable footage: still a film.
        self.assertTrue(matcher.plan([scene], [gone])[0][1])

    def test_a_corner_logo_is_cropped_away_not_blurred(self):
        logo = [0.02, 0.03, 0.12, 0.06]
        framed = safezone.frame_logos(None, [logo], "none")
        self.assertEqual(safezone.logo_on_screen(framed, logo), (0.0, 0.0))
        self.assertLessEqual(framed.zoom, safezone.MAX_ZOOM + 1e-9)
        # With a caption band, the band stays hidden too.
        band = safezone.Band(0.86, 0.95, 1.0)
        framed = safezone.frame_logos(band, [logo], "letterbox_235")
        self.assertEqual(safezone.logo_on_screen(framed, logo), (0.0, 0.0))
        self.assertLessEqual(safezone.window(framed)[3], band.top)
        # Nothing to crop: the plain framing.
        self.assertEqual(safezone.frame_logos(None, [], "none"), safezone.framing(None, "none"))

    def test_a_big_central_watermark_is_heavy(self):
        big = [0.35, 0.4, 0.3, 0.2]
        framed = safezone.frame_logos(None, [big], "none")
        self.assertTrue(safezone.is_heavy(framed, big))
        state = safezone.watermark_state(framed, [big], [[0.0, 70.0]], 100.0)
        self.assertEqual((state["heavy_watermark"], state["usable"]), (True, False))
        small = [0.45, 0.45, 0.04, 0.03]
        self.assertFalse(safezone.is_heavy(safezone.frame_logos(None, [small], "none"), small))

    def test_the_renderer_moves_the_crop_and_skips_cropped_logos(self):
        framing = {"zoom": 1.14, "shift": 0.0, "letterbox": 0.0, "clear": True, "shift_x": 0.0614, "logos": [[0.0, 0.0, 0.05, 0.05]]}
        graph = roughcut.video_filter(1920, 1080, framing, frames=30, ken_burns=False)
        self.assertNotIn("gblur", graph)
        self.assertIn("crop=1684:948:236:", graph)

    def test_source_limits(self):
        self.assertEqual((MIN_SOURCES, MAX_SOURCES), (2, 15))


class MapTests(unittest.TestCase):
    def test_target_and_readout(self):
        self.assertEqual(mapgen.target({"target_Coordinates_3D": {"lat": 30.3907, "lng": 114.8946, "location_name": "Ezhou"}}),
                         {"lat": 30.3907, "lon": 114.8946, "label": "Ezhou"})
        self.assertEqual(mapgen.target({"target_Coordinates_3D": [30.3907, 114.8946]})["label"], "")
        for bad in (None, {"lat": "x", "lng": 1}, [0, 0], [95, 10], "30,114"):
            self.assertIsNone(mapgen.target({"target_Coordinates_3D": bad}))
        self.assertEqual(mapgen.readout(30.3907, 114.8946), "30.3907° N, 114.8946° E")
        self.assertEqual(mapgen.readout(-33.8, -70.5), "33.8000° S, 70.5000° W")

    def test_tiles_are_fetched_once_and_offline_gives_up_fast(self):
        with TemporaryDirectory() as temp:
            calls = []

            def fetch(url, timeout):
                calls.append(url)
                return b"\xff\xd8 fake jpeg"
            state = mapgen.fetch_tiles(30.39, 114.89, Path(temp), fetch)
            self.assertTrue(state["satellite"])
            self.assertEqual(len(calls), len(mapgen.LEVELS) * mapgen.GRID ** 2)
            self.assertTrue(all("World_Imagery/MapServer/tile/" in url for url in calls))
            mapgen.fetch_tiles(30.39, 114.89, Path(temp), fetch)
            self.assertEqual(len(calls), len(mapgen.LEVELS) * mapgen.GRID ** 2, "kept, not fetched again")
        with TemporaryDirectory() as temp:
            tries = []

            def offline(url, timeout):
                tries.append(url)
                raise OSError("no network")
            state = mapgen.fetch_tiles(30.39, 114.89, Path(temp), offline)
            self.assertFalse(state["satellite"])
            self.assertEqual(len(tries), mapgen.GIVE_UP)

    def test_placement_after_the_opening_shot(self):
        offset, length = mapgen.placement(12.0, 1.8)
        self.assertTrue(1.8 <= offset <= matcher.MAX_SLICE and mapgen.MIN_SECONDS <= length <= mapgen.MAX_SECONDS)
        after = 12.0 - offset - length
        self.assertTrue(after == 0 or after >= 1.8)
        self.assertIsNone(mapgen.placement(5.0, 1.8))

    def test_the_hook_gets_the_map_as_its_second_slice(self):
        script_value = {"target_Coordinates_3D": {"lat": 30.39, "lng": 114.89, "location_name": "Ezhou"},
                        "chapters": [{"chapter_type": "HOOK", "scenes": [
                            {"scene_id": "sc_001", "narration_text": "A tower.", "matched_source_refs": []},
                            {"scene_id": "sc_002", "narration_text": "Big.", "matched_source_refs": [],
                             "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Area", "value": "197.7 acres",
                                                   "subtext": "about 150 American football fields"}}]},
                                     {"chapter_type": "PROBLEM", "scenes": [
                            {"scene_id": "sc_003", "narration_text": "Small farms.", "matched_source_refs": []},
                            {"scene_id": "sc_004", "narration_text": "Giants.", "matched_source_refs": [{"source_id": "src_01", "start": 0, "end": 9}],
                             "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Top 20", "value": "58%", "subtext": "of the market"}}]}]}
        timeline = {"scenes": [{"scene_id": f"sc_{n:03d}", "chapter_type": kind, "start": start, "slot_end": start + 12.0}
                               for n, (kind, start) in enumerate((("HOOK", 0.0), ("HOOK", 12.0), ("PROBLEM", 24.0), ("PROBLEM", 36.0)), 1)]}
        scenes, drawn = visual.add_graphics(visual._scenes(script_value, timeline), script_value, timeline, mapgen.target(script_value))
        self.assertEqual(drawn["map_01"]["kind"], "map")
        self.assertEqual(scenes[0].hold, "first")
        source = matcher.Source("src_01", 300.0, tuple(float(t) for t in range(7, 300, 7)))
        planned = matcher.plan(scenes, [source])
        first = planned[0][1]
        self.assertEqual([piece.kind for piece in first[:2]], ["footage", "map"])
        # The hook's area card sits next to the map, so the slide goes to the market share in the Problem act.
        kinds = {piece.ref: piece.kind for _, pieces, _ in planned for piece in pieces if piece.kind != "footage"}
        self.assertEqual(sorted(kinds.values()), ["infographic", "map"])
        self.assertEqual(next(item for item in drawn.values() if item["kind"] == "share")["scene_id"], "sc_004")
        for scene, pieces, _ in planned:
            self.assertAlmostEqual(sum(piece.duration for piece in pieces), scene.end - scene.start, places=2)


class SlideTests(unittest.TestCase):
    def test_classify(self):
        compare = infographic.classify({"type": "METRIC_CALLOUT", "title": "Loss per pig", "value": "about $70", "subtext": "cost $238, price $168"})
        self.assertEqual(compare["kind"], "compare")
        self.assertEqual([(item["label"], item["amount"], item["text"]) for item in compare["items"]], [("Cost", 238.0, "$238"), ("Price", 168.0, "$168")])
        self.assertEqual(infographic.classify({"value": "58%", "subtext": "of China's pork market"})["percent"], 58.0)
        scale = infographic.classify({"value": "1,323 tons", "subtext": "about 60 truckloads"})
        self.assertEqual((scale["kind"], scale["count"], scale["icon"]), ("scale", 60.0, "truck"))
        self.assertEqual(infographic.classify({"value": "$560 million", "subtext": "two towers"})["kind"], "figure")
        for plain in ({"value": "about 1,500", "subtext": "10 to 12 staff per floor"}, {"type": "LOWER_THIRD", "value": "58%"}, {"value": "11 tons"}):
            self.assertIsNone(infographic.classify(plain))

    def test_select_limits(self):
        card = {"type": "METRIC_CALLOUT", "title": "x", "value": "40%", "subtext": "y"}
        scenes = [{"chapter_type": kind, "on_screen_graphic": card, "slot": 9.0} for kind in ["PROBLEM"] * 6 + ["CRITICAL_EVALUATION"] * 6]
        chosen = infographic.select(scenes)
        self.assertLessEqual(len(chosen), infographic.MAX_SLIDES)
        indexes = sorted(chosen)
        self.assertTrue(all(b - a >= 2 for a, b in zip(indexes, indexes[1:])))
        self.assertLessEqual(sum(1 for index in chosen if index < 6), infographic.PER_ACT)
        self.assertEqual(infographic.select([{"chapter_type": "PROBLEM", "on_screen_graphic": card, "slot": 3.0}]), {})

    def test_slide_ass_draws_every_kind(self):
        for graphic in ({"value": "about $70", "title": "Loss", "subtext": "cost $238, price $168"}, {"value": "58%", "title": "Share", "subtext": "of it"},
                        {"value": "197.7 acres", "title": "Area", "subtext": "about 150 American football fields"},
                        {"value": "$560 million", "title": "Cost", "subtext": "two towers"}):
            slide = infographic.classify(graphic)
            text = infographic.slide_ass(slide, 4.0, 138)
            self.assertIn("[Events]", text)
            self.assertIn(slide["value"].split()[0].replace("$", "").replace("%", "")[:2], text)


class SoundTests(unittest.TestCase):
    def test_ui_cues_are_mixed(self):
        built = soundscape.ambience_argv(Path("ffmpeg"), {"chapters": []}, {"chapters": [], "scenes": []}, 20.0, Path("out.wav"),
                                         (), [("telemetry_sweep", 3.0), ("hud_slide_tick", 9.5), ("unknown", 1.0), ("hud_slide_tick", 30.0)])
        argv, _ = built
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("adelay=3000", graph)
        self.assertIn("adelay=9500", graph)
        self.assertEqual(graph.count("adelay"), 2)
        self.assertIsNone(soundscape.ambience_argv(Path("ffmpeg"), {"chapters": []}, {"chapters": [], "scenes": []}, 20.0, Path("o.wav")))

    def test_cues_follow_the_plan_and_the_hero_cards(self):
        plan = {"scenes": [{"slices": [{"at": 0.0, "start": 0, "end": 5}, {"kind": "map", "at": 5.0, "start": 0, "end": 4.5, "duration": 4.5}]},
                           {"slices": [{"kind": "infographic", "at": 20.0, "start": 0, "end": 4.0, "duration": 4.0}]}]}
        hero = overlays.Event(12.6, 18.0, "Accent", "x", (0, 0), overlays.HERO_SLIDE)
        self.assertEqual(roughcut.ui_cues(plan, [hero]), [("telemetry_sweep", 5.0), ("hud_slide_tick", 12.6), ("hud_slide_tick", 20.05)])
        self.assertEqual(roughcut.generated_spans(plan), [(5.0, 9.5), (20.0, 24.0)])

    def test_cards_keep_off_generated_pictures(self):
        card = overlays.Event(4.0, 10.0, "CardValue", "58%")
        caption = overlays.Event(4.0, 10.0, "Caption", "words")
        kept = overlays.clear_spans([card, caption], [(4.5, 9.5)])
        self.assertEqual([(event.style, event.start, event.end) for event in kept], [("Caption", 4.0, 10.0)])
        late = overlays.clear_spans([overlays.Event(6.0, 14.0, "CardValue", "x")], [(5.0, 9.5)])
        self.assertEqual((late[0].start, late[0].end), (9.5, 14.0))


try:
    import numpy  # noqa: F401
    NUMPY = True
except ImportError:
    NUMPY = False


@unittest.skipUnless(FFMPEG and FFPROBE and NUMPY, "ffmpeg or numpy not available")
class RenderTests(unittest.TestCase):
    def test_real_render_with_map_and_slide(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_run(base)
            spec = load_spec(spec_path(run))
            from engine.dubflow.docu import context
            context.build_all(run, spec, lambda path: 8_000)
            for name in ("a.mp4", "b.mp4"):
                subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
                                "-pix_fmt", "yuv420p", str(base / name)], check=True, capture_output=True)
            draft = {"target_Coordinates_3D": {"lat": 30.39, "lng": 114.89, "location_name": "Ezhou"},
                     "chapters": [{"chapter_type": "HOOK", "scenes": [{"narration_text": "The tower rises.", "matched_source_refs": []}]},
                                  {"chapter_type": "PROBLEM", "scenes": [{"narration_text": "The giants won.", "matched_source_refs": [],
                                                                          "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Top 20",
                                                                                                "value": "58%", "subtext": "of the market"}}]}]}
            checked = script.check(draft, spec, script.load_contexts(run, spec))
            write_json(run / "documentary_script.json", checked)
            write_json(run / "master_audio_timeline.json", {
                "script_sha256": checked["script_sha256"], "duration": 6.0,
                "chapters": [{"chapter_type": "HOOK", "start": 0.0, "end": 2.5, "slot_end": 3.0}, {"chapter_type": "PROBLEM", "start": 3.0, "end": 6.0, "slot_end": 6.0}],
                "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 2.5, "slot_end": 3.0}, {"scene_id": "sc_002", "start": 3.0, "end": 6.0, "slot_end": 6.0}]})
            write_json(run / "visual_plan.json", {
                "script_sha256": checked["script_sha256"], "timeline_sha256": roughcut._sha256(run / "master_audio_timeline.json"), "duration": 6.0,
                "sources": [{"id": "src_01", "framing": {"zoom": 1.0, "shift": 0.0, "letterbox": 0.1217, "clear": True}},
                            {"id": "src_02", "framing": {"zoom": 1.0, "shift": 0.0, "letterbox": 0.1217, "clear": True}}],
                "graphics": {"map_01": {"kind": "map", "lat": 30.39, "lon": 114.89, "label": "Ezhou", "scene_id": "sc_001"},
                             "info_01": {"kind": "share", "title": "Top 20", "value": "58%", "subtext": "of the market", "percent": 58.0,
                                         "scene_id": "sc_002", "backdrop": {"source_id": "src_02", "at": 2.0}}},
                "scenes": [{"scene_id": "sc_001", "slices": [{"source_id": "src_01", "start": 1.0, "end": 2.0, "at": 0.0},
                                                             {"source_id": "", "start": 0.0, "end": 2.0, "at": 1.0, "kind": "map", "ref": "map_01"}]},
                           {"scene_id": "sc_002", "slices": [{"source_id": "src_02", "start": 0.0, "end": 3.0, "at": 3.0, "kind": "infographic",
                                                              "ref": "info_01", "duration": 3.0}]}]})
            with wave.open(str(run / "narration.wav"), "wb") as handle:
                handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000)
                handle.writeframes(b"\x00\x00" * 48000 * 6)
            overlays.save_options(run, music="none")
            result = roughcut.run(run, spec, ffmpeg=Path(FFMPEG), probe=lambda video: roughcut.probe_display(Path(FFPROBE), video),
                                  encoders=("libopenh264", "h264_mf", "libx264", "mpeg4"))
            self.assertEqual(result["state"], "COMPLETED", result)
            receipt = json.loads((run / ".dubflow-work/render/slice-0002.json").read_text(encoding="utf-8"))
            self.assertFalse(receipt["drawn"]["map"]["satellite"], "offline: the vector chart")
            frames = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                                "stream=nb_read_frames", "-of", "json", str(run / "documentary.mp4")],
                                               capture_output=True, text=True).stdout)["streams"][0]["nb_read_frames"]
            self.assertEqual(int(frames), 180)
            self.assertTrue((run / ".dubflow-work/render/ambience.wav").is_file(), "the UI sounds play even without room tone")
            # A second render reuses every slice.
            before = (run / ".dubflow-work/render/slice-0003.mp4").stat().st_mtime_ns
            self.assertEqual(roughcut.run(run, spec, ffmpeg=Path(FFMPEG), probe=lambda video: roughcut.probe_display(Path(FFPROBE), video),
                                          encoders=("libopenh264", "h264_mf", "libx264", "mpeg4"))["state"], "COMPLETED")
            self.assertEqual((run / ".dubflow-work/render/slice-0003.mp4").stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()

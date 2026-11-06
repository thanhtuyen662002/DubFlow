"""The documentary's Tier-2 upgrade (#548): story rules, pacing, room tone, keyword B-roll, soft cover, hero callouts."""
from __future__ import annotations

from array import array
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.docu import intake, keywords, matcher, narration, overlays, roughcut, safezone, script, soundscape, visual
from engine.dubflow.docu.project import load_spec, spec_path, write_json

from test_docu import make_run

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


class StoryRuleTests(unittest.TestCase):
    """Item 1: the page's prompt and the MCP brief carry the same mystery structure."""

    def test_mcp_brief_and_page_prompt_say_the_same(self):
        page = (ROOT / "apps/desktop/src/features/docu/ai.ts").read_text(encoding="utf-8")
        joined = re.sub(r'"\s*\n\s*\+\s*"', "", page)
        for rule in intake.STORY_RULES:
            self.assertIn(json.dumps(rule)[1:-1], page)
        for chapter, purpose in intake.PURPOSE.items():
            self.assertIn(f"{chapter}: {json.dumps(purpose)}", joined)
        self.assertIn("fatal paradox", intake.PURPOSE["HOOK"])
        self.assertIn("3A", intake.PURPOSE["ENGINEERING_SOLUTION"])
        self.assertIn("3C", intake.PURPOSE["ENGINEERING_SOLUTION"])
        self.assertIn("never signals an ending", intake.PURPOSE["CRITICAL_EVALUATION"])
        self.assertTrue(all(rule in intake.RULES for rule in intake.STORY_RULES))
        # The act shares stay as they were: the script checker and the page budget them the same.
        self.assertEqual(intake.CHAPTER_SHARES, {"HOOK": 0.07, "PROBLEM": 0.20, "ENGINEERING_SOLUTION": 0.50, "CRITICAL_EVALUATION": 0.23})

    def test_stock_phrases_are_flagged_but_numbers_still_checked(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            from engine.dubflow.docu import context
            spec = load_spec(spec_path(run))
            context.build_all(run, spec, lambda path: 20_000)
            draft = {"chapters": [{"chapter_type": "HOOK", "scenes": [
                {"narration_text": "This tower is a testament to engineering. Let us delve into it.", "matched_source_refs": []},
                {"narration_text": "But the concrete alone weighed 999,999 tons.", "matched_source_refs": []}]}]}
            checked = script.check(draft, spec, script.load_contexts(run, spec))
            first, second = checked["chapters"][0]["scenes"]
            self.assertTrue(any("testament, delve" in note for note in first["review_notes"]))
            self.assertTrue(second["needs_review"])
            self.assertTrue(any("999,999" in note for note in second["review_notes"]))
        self.assertEqual(script.cliches("Marvels, a TAPESTRY. In conclusion, to sum up."), ["marvels", "tapestry", "in conclusion", "to sum up"])
        self.assertEqual(script.cliches("Therefore the testing went on."), [])


class PacingTests(unittest.TestCase):
    """Item 2: shorter, varied pauses and a click-free tail on every piece."""

    def test_pauses(self):
        self.assertEqual((narration.SENTENCE_PAUSE, narration.EMPHATIC_PAUSE, narration.SCENE_PAUSE, narration.CHAPTER_PAUSE),
                         (0.22, 0.35, 0.40, 1.0))
        self.assertEqual(narration.sentence_pause("Why stack pigs?"), narration.EMPHATIC_PAUSE)
        self.assertEqual(narration.sentence_pause("It worked!”"), narration.EMPHATIC_PAUSE)
        self.assertEqual(narration.sentence_pause("It worked."), narration.SENTENCE_PAUSE)
        value = {"chapters": [{"chapter_type": "HOOK", "scenes": [{"scene_id": "sc_001", "narration_text": "Why build up? Land is scarce. So they did."},
                                                                   {"scene_id": "sc_002", "narration_text": "But it smelled."}]},
                              {"chapter_type": "PROBLEM", "scenes": [{"scene_id": "sc_003", "narration_text": "Waste piled up."}]}]}
        self.assertEqual([piece.pause for piece in narration.plan(value)], [0.35, 0.22, 0.40, 1.0, 0.0])

    def test_tail_fade_is_sample_exact(self):
        frames = array("h", [1000] * 2000).tobytes()
        faded = array("h")
        faded.frombytes(narration.fade_tail(frames, 24_000))
        self.assertEqual(len(faded), 2000)
        self.assertEqual(faded[0], 1000)
        self.assertEqual(faded[-1], 0)
        fade = round(24_000 * narration.TAIL_FADE)
        self.assertEqual(faded[2000 - fade - 1], 1000)
        self.assertTrue(all(faded[i] >= faded[i + 1] for i in range(2000 - fade, 1999)))


def _scene_script(tags: list[str | None]) -> dict:
    return {"chapters": [{"chapter_type": "HOOK", "scenes": [{"scene_id": f"sc_{i:03d}", "ambient_sfx_tag": tag} for i, tag in enumerate(tags[:2], 1)]},
                         {"chapter_type": "PROBLEM", "scenes": [{"scene_id": f"sc_{i:03d}", "ambient_sfx_tag": tag} for i, tag in enumerate(tags[2:], 3)]}]}


TIMELINE = {"chapters": [{"chapter_type": "HOOK", "start": 0.0, "end": 12.0}, {"chapter_type": "PROBLEM", "start": 13.0, "end": 24.0}],
            "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 5.8}, {"scene_id": "sc_002", "start": 6.2, "end": 12.0},
                       {"scene_id": "sc_003", "start": 13.0, "end": 18.0}, {"scene_id": "sc_004", "start": 18.4, "end": 24.0}]}


class SoundscapeTests(unittest.TestCase):
    """Item 3: each scene's ambient tag under its narration, act hits, and the new bed."""

    def test_stretches_join_one_tag_and_skip_untagged(self):
        stretches = soundscape.stretches(_scene_script(["wind_high_altitude", "wind_high_altitude", None, "data_beeps"]), TIMELINE)
        self.assertEqual(stretches, [("wind_high_altitude", 0.0, 12.0), ("data_beeps", 18.4, 24.0)])
        self.assertEqual(soundscape.act_hits(TIMELINE), [12.0])

    def test_graph_places_sounds_hits_and_prefers_files(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "data_beeps.wav").write_bytes(b"RIFF")
            argv, files = soundscape.ambience_argv(Path("ffmpeg"), _scene_script(["wind_high_altitude", "wind_high_altitude", None, "data_beeps"]),
                                                   TIMELINE, 25.0, folder / "out.wav", [folder])
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertEqual(files, [folder / "data_beeps.wav"])
        self.assertIn(str(folder / "data_beeps.wav"), argv)
        self.assertIn("loudnorm=I=-31", graph)
        self.assertIn("anoisesrc=r=48000:c=pink", " ".join(argv))
        self.assertIn("clip((t-0.000)/0.5,0,1)*clip((12.000-t)/0.5,0,1)", graph)
        self.assertIn("adelay=12000:all=1", graph)
        self.assertIsNone(soundscape.ambience_argv(Path("ffmpeg"), _scene_script([None] * 4), {"chapters": [], "scenes": []}, 10.0, Path("x.wav")))

    def test_bed_layers_and_mix(self):
        expression = overlays.mood_expression("dark_suspense")
        self.assertIn("asin(sin(", expression)
        self.assertNotEqual(expression, overlays.mood_expression("curious_pulse"))
        self.assertIn("exp(-4.5*mod(t*0.4333,1))", overlays.pulse_expression("dark_suspense"))
        argv = overlays.bed_argv(Path("ffmpeg"), [("cinematic_tension_drone", 0.0, 20.0), ("curious_pulse", 20.0, 40.0)], 40.0, Path("bed.wav"))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn("lowpass=f=380", graph)
        self.assertIn("lowpass=f=600", graph)
        self.assertIn("[pad1][sub1]amix", graph)
        self.assertEqual(overlays.ducked_mix(bed=False, ambience=2), "[1:a]apad[voice];[2:a]anull[room];[voice][room]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[audio]")
        self.assertIn("[3:a]anull[room]", overlays.ducked_mix(bed=True, ambience=3))
        self.assertEqual(overlays.load_options(Path("/nonexistent"))["sfx"], True)

    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_real_room_tone_levels(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "ambience.wav"
            argv, _ = soundscape.ambience_argv(Path(FFMPEG), _scene_script(["heavy_machinery_hum"] * 2 + [None, None]), TIMELINE, 25.0, out)
            subprocess.run(argv, check=True, capture_output=True)
            with wave.open(str(out), "rb") as handle:
                self.assertEqual((handle.getnchannels(), handle.getframerate()), (2, 48_000))
                self.assertAlmostEqual(handle.getnframes() / 48_000, 25.0, places=2)

            def loudness(start: float, seconds: float) -> float:
                text = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-ss", str(start), "-t", str(seconds), "-i", str(out),
                                       "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
                return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", text)[-1])
            self.assertAlmostEqual(loudness(2.0, 8.0), soundscape.SFX_LUFS, delta=1.5)
            self.assertAlmostEqual(loudness(12.0, soundscape.HIT_SECONDS), soundscape.HIT_LUFS, delta=2.0)
            self.assertLess(loudness(18.0, 5.0), -60)


class KeywordTests(unittest.TestCase):
    """Item 4: filler footage follows what the scene talks about."""

    def test_concepts_bridge_english_and_chinese(self):
        self.assertTrue({"biogas", "manure"} <= keywords.concepts("产生的粪污进入沼气池发酵"))
        self.assertTrue({"biogas", "manure", "#1100"} <= keywords.concepts("Its 1,100 tons of manure feed a biogas digester."))
        self.assertIn("elevator", keywords.concepts("大型电梯"))
        self.assertNotIn("energy", keywords.concepts("大型电梯"))
        self.assertEqual(keywords.scene_terms(["biogas plant"], "The manure goes down."), (("biogas", 2), ("manure", 1)))

    def _sources(self):
        lines = {"src_02": ((10.0, 14.0, keywords.concepts("中控室里的电脑屏幕")),),
                 "src_03": ((40.0, 44.0, keywords.concepts("粪污进入沼气罐发酵")),)}
        return [matcher.Source("src_01", 30.0, (10.0, 20.0)),
                matcher.Source("src_02", 60.0, (10.0, 20.0, 30.0, 40.0), lines=lines["src_02"]),
                matcher.Source("src_03", 60.0, (10.0, 20.0, 30.0, 40.0), lines=lines["src_03"])]

    def test_filler_prefers_footage_about_the_scene(self):
        terms = keywords.scene_terms(["biogas tanks", "manure"], "So the waste becomes gas.")
        # Nothing cited: every candidate is filler.
        scene = matcher.Scene("sc_001", 0.0, 4.0, (), terms=terms)
        planned = matcher.plan([scene], self._sources())
        piece = planned[0][1][0]
        self.assertEqual((piece.source_id, piece.start), ("src_03", 40.0 + matcher.SHOT_MARGIN))
        # Without terms the old order holds: the first source by id.
        plain = matcher.plan([matcher.Scene("sc_001", 0.0, 4.0, ())], self._sources())
        self.assertNotEqual(plain[0][1][0].source_id, "src_03")
        # Deterministic.
        self.assertEqual(matcher.plan([scene], self._sources()), planned)

    def test_cited_footage_still_comes_first(self):
        terms = keywords.scene_terms(["biogas"], "Gas.")
        scene = matcher.Scene("sc_001", 0.0, 4.0, (matcher.Ref("src_02", 20.0, 26.0),), terms=terms)
        piece = matcher.plan([scene], self._sources())[0][1][0]
        self.assertEqual((piece.source_id, piece.tier), ("src_02", matcher.CITED))

    def test_visual_reads_tags_and_lines(self):
        value = {"chapters": [{"chapter_type": "HOOK", "scenes": [{"scene_id": "sc_001", "narration_text": "Manure piles up.",
                                                                    "visual_search_tags": ["biogas tank"], "matched_source_refs": []}]}]}
        scenes = visual._scenes(value, {"scenes": [{"scene_id": "sc_001", "chapter_type": "HOOK", "start": 0.4, "slot_end": 5.0}]})
        self.assertEqual(scenes[0].terms, (("biogas", 2), ("manure", 1)))
        lines = visual.source_lines({"segments": [{"start": 3.0, "end": 4.0, "original_text": "沼气"}, {"start": 1.0, "end": 2.0, "original_text": "你好"}]})
        self.assertEqual(lines, ((3.0, 4.0, frozenset({"biogas"})),))


class CoverTests(unittest.TestCase):
    """Item 5: a band framing cannot clear is blurred and darkened, its top feathered."""

    def test_cover_box(self):
        self.assertEqual(safezone.cover_box(safezone.Band(0.8, 0.95, 1.0), 1080), (826, 38, 200))
        self.assertEqual(safezone.cover_box(safezone.Band(0.01, 0.1, 1.0), 1080), (0, 10, 108))
        self.assertIsNone(safezone.cover_box(safezone.Band(0.9, 0.9, 1.0), 1080))

    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_real_cover_darkens_only_the_band(self):
        with TemporaryDirectory() as temp:
            graph = roughcut.video_filter(640, 360, {"zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": False,
                                                     "band": {"top": 0.8, "bottom": 0.95}}, frames=10, ken_burns=False)
            out = Path(temp) / "frame.gray"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=640x360:r=25:d=1", "-filter_complex",
                            graph + ";[video]scale=1920:1080,format=gray[g]", "-map", "[g]", "-frames:v", "1", "-f", "rawvideo", str(out)],
                           check=True, capture_output=True)
            pixels = out.read_bytes()
            row = lambda y: sum(pixels[y * 1920:(y + 1) * 1920]) / 1920  # noqa: E731
            self.assertGreater(row(500), 200)
            # Inside the band about 25% darker; just above it the feather is part way.
            self.assertLess(row(960), 0.85 * row(500))
            self.assertLess(row(850), row(500))
            self.assertGreater(row(850), row(960))


class GraphicsTests(unittest.TestCase):
    """Item 6: the hero callout and the accent-bar card."""

    def test_hero_and_card(self):
        value = {"chapters": [
            {"chapter_type": "HOOK", "scenes": [
                {"scene_id": "sc_001", "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Pigs", "value": "650,000", "subtext": "a year"}},
                {"scene_id": "sc_002", "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Feed", "value": "2,000 tons", "subtext": ""}}]},
            {"chapter_type": "PROBLEM", "scenes": [
                {"scene_id": "sc_003", "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Site", "value": "197.7 acres",
                                                             "subtext": "≈ 150 American football fields"}},
                {"scene_id": "sc_004", "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "Floor", "value": "1 football field", "subtext": ""}}]}]}
        timeline = {"scenes": [{"scene_id": f"sc_00{i}", "start": (i - 1) * 10.0, "end": i * 10.0 - 0.5} for i in range(1, 5)]}
        events = overlays.graphic_events(value, timeline, 0)
        styles = [(event.style, event.text) for event in events if event.style in ("HeroValue", "CardValue")]
        # The hook's first card and the first landmark comparison of an act are heroes; one hero per act.
        self.assertEqual(styles, [("HeroValue", "650,000"), ("CardValue", "2,000 tons"), ("HeroValue", "197.7 acres"), ("CardValue", "1 football field")])
        self.assertEqual(sum(event.style == "Accent" for event in events), 4)
        rows = overlays.overlay_rows(events, 0.0, 3.0)
        self.assertTrue(any("\\move(" in row and "HeroValue" in row for row in rows))
        later = overlays.overlay_rows(events, 3.0, 3.0)
        self.assertFalse(any("\\move(" in row for row in later))
        self.assertTrue(any("{\\p1}m 0 0 l 8 0" in row for row in later))

    def test_midroll_hint_is_on_the_page(self):
        model = (ROOT / "apps/desktop/src/features/docu/model.ts").read_text(encoding="utf-8")
        page = (ROOT / "apps/desktop/src/features/docu/view.ts").read_text(encoding="utf-8")
        self.assertIn("Mẹo: Video từ 8–12 phút giúp bật quảng cáo giữa video (Mid-roll) trên YouTube và đủ thời lượng triển khai sâu 4 hồi.", model)
        self.assertIn('id="docu-minutes-hint"', page)


class RenderTests(unittest.TestCase):
    @unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
    def test_real_render_with_cover_room_tone_and_hit(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_run(base)
            spec = load_spec(spec_path(run))
            from engine.dubflow.docu import context
            context.build_all(run, spec, lambda path: 8_000)
            for name in ("a.mp4", "b.mp4"):
                subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=8",
                                "-pix_fmt", "yuv420p", str(base / name)], check=True, capture_output=True)
            draft = {"chapters": [{"chapter_type": "HOOK", "scenes": [{"narration_text": "The dam rises.", "matched_source_refs": [],
                                                                       "ambient_sfx_tag": "wind_high_altitude"}]},
                                  {"chapter_type": "PROBLEM", "scenes": [{"narration_text": "But the river fights back.", "matched_source_refs": [],
                                                                          "ambient_sfx_tag": "flowing_water"}]}]}
            checked = script.check(draft, spec, script.load_contexts(run, spec))
            write_json(run / "documentary_script.json", checked)
            write_json(run / "master_audio_timeline.json", {
                "script_sha256": checked["script_sha256"], "duration": 6.0,
                "chapters": [{"chapter_type": "HOOK", "start": 0.0, "end": 2.5, "slot_end": 3.5}, {"chapter_type": "PROBLEM", "start": 3.5, "end": 6.0, "slot_end": 6.0}],
                "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 2.5, "slot_end": 3.5}, {"scene_id": "sc_002", "start": 3.5, "end": 6.0, "slot_end": 6.0}]})
            write_json(run / "visual_plan.json", {
                "script_sha256": checked["script_sha256"], "timeline_sha256": roughcut._sha256(run / "master_audio_timeline.json"), "duration": 6.0,
                "sources": [{"id": "src_01", "framing": {"zoom": 1.14, "shift": 0.06, "letterbox": 0.0, "clear": False, "band": {"top": 0.8, "bottom": 0.95}}},
                            {"id": "src_02", "framing": {"zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True}}],
                "scenes": [{"scene_id": "sc_001", "slices": [{"source_id": "src_01", "start": 1.0, "end": 4.0, "at": 0.0}]},
                           {"scene_id": "sc_002", "slices": [{"source_id": "src_02", "start": 2.0, "end": 5.0, "at": 3.0}]}]})
            with wave.open(str(run / "narration.wav"), "wb") as handle:
                handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000)
                handle.writeframes(b"\x00\x00" * 48000 * 6)
            overlays.save_options(run, music="none")
            result = roughcut.run(run, spec, ffmpeg=Path(FFMPEG), probe=lambda video: roughcut.probe_display(Path(FFPROBE), video),
                                  encoders=("libopenh264", "h264_mf", "libx264", "mpeg4"))
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertTrue((run / ".dubflow-work/render/ambience.wav").is_file())
            text = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(run / "documentary.mp4"), "-af", "ebur128", "-f", "null", "-"],
                                  capture_output=True, text=True).stderr
            # The narration is silent here: what plays is the room tone and the hit.
            self.assertGreater(float(re.findall(r"I:\s+(-?[\d.]+) LUFS", text)[-1]), -40)
            frames = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                                                "stream=nb_read_frames", "-of", "json", str(run / "documentary.mp4")],
                                               capture_output=True, text=True).stdout)["streams"][0]["nb_read_frames"]
            self.assertEqual(int(frames), 180)


class LogoBlurTests(unittest.TestCase):
    @unittest.skipUnless(FFMPEG, "ffmpeg not available")
    def test_bold_white_logo_is_washed_out_and_the_blur_has_no_hard_edge(self):
        # "太空记" in src_02 (2026-11-05): bold white strokes and a thin outline, still readable under sigma 12.
        source = ("color=c=0x406080:s=1920x1080:d=1,format=rgb24,drawbox=x=120:y=770:w=40:h=60:color=white:t=fill,"
                  "drawbox=x=180:y=770:w=12:h=60:color=white:t=fill,drawbox=x=220:y=770:w=60:h=10:color=white:t=fill,"
                  "drawbox=x=117:y=860:w=230:h=4:color=white:t=fill")
        graph = roughcut.video_filter(1920, 1080, {"clear": True, "logos": [[0.0326, 0.6283, 0.1738, 0.2267]]}, frames=1, ken_burns=False)
        with TemporaryDirectory() as temp:
            out = Path(temp) / "frame.gray"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-filter_complex", graph, "-map", "[video]",
                            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", str(out)], check=True)
            data = out.read_bytes()
        pixel = lambda x, y: data[y * 1920 + x]
        box = [pixel(x, y) for y in range(680, 920, 4) for x in range(60, 394, 4)]
        self.assertLess(max(box) - min(box), 50, "white strokes (255 on 90) are washed out")
        # Walking out of the box, no step between neighbouring pixels is bigger than a few levels.
        row = [pixel(x, 800) for x in range(300, 520)]
        column = [pixel(200, y) for y in range(560, 760)]
        self.assertLess(max(abs(a - b) for a, b in zip(row, row[1:])), 4)
        self.assertLess(max(abs(a - b) for a, b in zip(column, column[1:])), 4)
        # Over a busy background the patch must not end on a visible edge either (0:30 of the tier-2 film): the fade
        # from blurred to sharp spans well over 100 px.
        mask = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", graph.split("[logo0mask]")[0].split(";")[-1]
                               .replace("format=gray", "format=gray"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                              capture_output=True, check=True).stdout
        width = int(re.search(r"color=c=black:s=(\d+)x", graph).group(1))
        middle = mask[(len(mask) // width // 2) * width:][:width]
        fade = [x for x in range(width) if 10 < middle[x] < 245]
        self.assertGreater(len(fade), 100, len(fade))
        # ...but the whole box stays fully covered: the triangle apex at y 0.637 (t 346.5 of the tier-2 film).
        top = int(re.search(r"crop=\d+:\d+:\d+:(\d+),gblur", graph).group(1))
        self.assertGreaterEqual(mask[(int(0.637 * 1080) - top) * width + width // 3], 250)


if __name__ == "__main__":
    unittest.main()

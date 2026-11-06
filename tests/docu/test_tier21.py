"""The documentary's Tier-2.1 patch: no ending signposts, repeats framed differently, the ambience switch."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import __main__ as cli
from engine.dubflow.docu import intake, matcher, overlays, roughcut, script
from engine.dubflow.docu.project import load_spec, spec_path, work_dir, write_json

from test_docu import make_run
from test_roughcut import _project

ROOT = Path(__file__).resolve().parents[2]


class SignpostTests(unittest.TestCase):
    """Item 1: "The verdict:" and friends tell the viewer the film is about to end."""

    def test_prompts_ban_the_labels(self):
        page = (ROOT / "apps/desktop/src/features/docu/ai.ts").read_text(encoding="utf-8")
        for phrase in ("the verdict", "final verdict", "the bottom line", "all in all", "at the end of the day"):
            self.assertIn(phrase, intake.STORY_RULES[1])
        self.assertIn('\\"The verdict:\\"', page)
        self.assertIn('"The verdict:"', intake.PURPOSE["CRITICAL_EVALUATION"])

    def test_labels_are_flagged_and_stripped_from_the_last_act(self):
        self.assertEqual(script.cliches("All in all, at the end of the day the bottom line holds."),
                         ["all in all", "at the end of the day", "the bottom line"])
        self.assertEqual(script.strip_signposts("The verdict: the pig tower solves land."), "the pig tower solves land.")
        self.assertEqual(script.strip_signposts("Costs rose. Final verdict — it works."), "Costs rose. it works.")
        self.assertEqual(script.strip_signposts("The bottom line: margins vanish."), "margins vanish.")
        self.assertEqual(script.strip_signposts("Nobody reached a verdict: yet."), "Nobody reached a verdict: yet.")
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            from engine.dubflow.docu import context
            spec = load_spec(spec_path(run))
            context.build_all(run, spec, lambda path: 20_000)
            line = "The verdict: the pig tower solves the land problem but not the disease problem."
            draft = {"chapters": [{"chapter_type": "HOOK", "scenes": [{"narration_text": line, "matched_source_refs": []}]},
                                  {"chapter_type": "CRITICAL_EVALUATION", "scenes": [{"narration_text": line, "matched_source_refs": []}]}]}
            checked = script.check(draft, spec, script.load_contexts(run, spec))
            hook, verdict = checked["chapters"][0]["scenes"][0], checked["chapters"][1]["scenes"][0]
            # Only the last act loses the label; elsewhere it is flagged for a rewrite.
            self.assertTrue(hook["narration_text"].startswith("The verdict:"))
            self.assertTrue(any("the verdict" in note for note in hook["review_notes"]))
            self.assertEqual(verdict["narration_text"], "The pig tower solves the land problem but not the disease problem.")
            self.assertFalse(any("the verdict" in note for note in verdict["review_notes"]))
            self.assertTrue(any("The verdict:" in note for note in checked["review_notes"]))


class ReusedSliceTests(unittest.TestCase):
    """Item 2: footage shown before is marked in the plan and framed differently in the rough cut."""

    def test_repeats_are_marked(self):
        # 30 s of footage for a 40 s scene: the last slices must repeat what the first ones showed.
        scene = matcher.Scene("sc_001", 0.0, 40.0, (matcher.Ref("src_01", 0.0, 30.0),))
        pieces = matcher.plan([scene], [matcher.Source("src_01", 30.0, tuple(float(t) for t in range(3, 30, 3)))])[0][1]
        fresh = [piece for piece in pieces if not piece.reused]
        repeats = [piece for piece in pieces if piece.reused]
        self.assertTrue(fresh and repeats)
        self.assertTrue(all(piece.tier == matcher.REUSED for piece in repeats))
        self.assertTrue(all(piece.tier != matcher.REUSED for piece in fresh))

    def test_shown_share(self):
        used = matcher._Used()
        used.add("a", 0.0, 4.0)
        used.add("a", 2.0, 5.0)
        self.assertAlmostEqual(used.shown("a", 3.0, 7.0), 0.5)
        self.assertEqual(used.shown("b", 3.0, 7.0), 0.0)

    def test_repeat_gets_a_tighter_crop_and_a_pull_out(self):
        framing = {"zoom": 1.14, "shift": 0.06, "letterbox": 0.1217, "clear": True}
        first = roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True)
        again = roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True, reused=1)
        other = roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True, reused=-1)
        self.assertIn("zoompan=z='1+0.04*on/89'", first)
        self.assertIn("zoompan=z='1.0577-0.0577*on/89'", again)
        self.assertNotIn("hflip", again)
        crop = lambda graph: [int(v) for v in graph.split("crop=", 1)[1].split(",", 1)[0].split(":")]
        w, h, x, y = crop(first)
        w2, h2, x2, y2 = crop(again)
        self.assertAlmostEqual(w / w2, 1.04, places=2)
        # Inside the planned crop (the safe framing), above its centre and to one side; the other repeat to the other side.
        self.assertTrue(x <= x2 and x2 + w2 <= x + w and y <= y2 and y2 + h2 <= y + h)
        self.assertLess(y2 - y, (h - h2) / 2)
        self.assertNotEqual(crop(other)[2], x2)
        # Without motion (Ken Burns turned off): a still crop about 9 % tighter.
        still = roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True, zoom_push=0, reused=1)
        self.assertNotIn("zoompan", still)
        self.assertAlmostEqual(w / crop(still)[0], 1.09, places=2)

    def test_old_plans_render_as_before(self):
        framing = {"zoom": 1.0, "shift": 0.0}
        self.assertEqual(roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True),
                         roughcut.video_filter(1920, 1080, framing, frames=90, ken_burns=True, reused=0))


class LegacyProjectTests(unittest.TestCase):
    """A 1.1.75 project (plan without ``reused``, render options without ``sfx``) loads and renders as before."""

    # The receipt key of a 1.1.75 slice: a different set of fields would re-render every old slice.
    KEY_1175 = {"recipe", "video", "start", "frames", "graph", "labels", "overlay"}

    def test_old_plan_and_options_render_unchanged(self):
        with TemporaryDirectory() as temp:
            run, spec, plan = _project(Path(temp))
            self.assertFalse(any("reused" in piece or "variation" in piece for scene in plan["scenes"] for piece in scene["slices"]))
            # render_options.json exactly as 1.1.75 wrote it: no sfx field.
            write_json(work_dir(run) / overlays.OPTIONS, {"graphics": True, "captions": False, "music": "generated", "music_path": None})
            self.assertTrue(overlays.load_options(run)["sfx"])
            calls = []

            def runner(argv, cwd, cancelled):
                calls.append(argv)
                Path(cwd, Path(argv[-1]).name).write_bytes(b"mp4")
            first = roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertEqual(first["state"], "COMPLETED", first)
            self.assertTrue(first["overlays"]["sfx"])
            render = run / ".dubflow-work" / "render"
            framings = {source["id"]: source["framing"] for source in plan["sources"]}
            for index, piece in enumerate(plan["scenes"][0]["slices"], 1):
                key = json.loads((render / f"slice-{index:04d}.json").read_text(encoding="utf-8"))["key"]
                self.assertEqual(set(key), self.KEY_1175)
                self.assertEqual(key["graph"], roughcut.video_filter(1920, 1080, framings[piece["source_id"]], frames=key["frames"],
                                                                     ken_burns=True, zoom_push=roughcut.KEN_BURNS_ZOOM))
                self.assertIn("zoompan=z='1+0.04*on/", key["graph"])
            # Rendered again: every slice keeps its receipt.
            calls.clear()
            roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            self.assertFalse(any(any(str(arg).startswith("slice-") for arg in argv) for argv in calls if "-ss" in argv))
            # A repeat marked in a new plan re-renders that slice only, with the other framing.
            plan["scenes"][0]["slices"][1].update({"reused": True, "variation": "alt_crop"})
            write_json(run / "visual_plan.json", plan)
            calls.clear()
            roughcut.run(run, spec, ffmpeg=Path("ffmpeg"), probe=lambda video: (1920, 1080), ffmpeg_runner=runner)
            sliced = [argv for argv in calls if "-ss" in argv]
            self.assertEqual(len(sliced), 1)
            self.assertTrue(str(sliced[0][-1]).startswith(str(render / "slice-0002.mp4")))
            key = json.loads((render / "slice-0002.json").read_text(encoding="utf-8"))["key"]
            self.assertEqual(key["variation"], "alt_crop")
            self.assertIn("zoompan=z='1.0577-0.0577", key["graph"])


class AmbienceOptionTests(unittest.TestCase):
    """Item 3: the page's "Âm thanh môi trường theo cảnh" switch reaches render_options.json."""

    def test_cli_saves_the_choice_and_keeps_it_when_left_out(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            self.assertTrue(overlays.load_options(run)["sfx"])
            self.assertEqual(cli.main(["options", "--run-dir", str(run), "--sfx", "false"]), 0)
            self.assertFalse(overlays.load_options(run)["sfx"])
            self.assertEqual(cli.main(["options", "--run-dir", str(run), "--graphics", "false", "--music", "none"]), 0)
            self.assertFalse(overlays.load_options(run)["sfx"])
            self.assertEqual(cli.main(["options", "--run-dir", str(run), "--sfx", "true"]), 0)
            self.assertTrue(overlays.load_options(run)["sfx"])


if __name__ == "__main__":
    unittest.main()

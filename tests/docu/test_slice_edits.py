"""Cuts of a documentary edited by hand in Chỉnh sửa (#630): trim, swap, remove, replace, own cover."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import roughcut, visual
from engine.dubflow.docu.project import DocuError, work_dir, write_json

from test_docu import make_run

ROOT = Path(__file__).resolve().parents[2]


def _plan(run: Path) -> None:
    write_json(run / "visual_plan.json", {
        "script_sha256": "s", "duration": 12.0, "cinematic_framing_preset": "none",
        "sources": [{"id": "src_01", "duration": 20.0, "framing": {"band": None, "zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True,
                                                                    "logos": []}},
                    {"id": "src_02", "duration": 9.0, "framing": {}}],
        "graphics": {"g1": {"kind": "map", "lat": 1.0, "lon": 2.0}},
        "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 8.0, "warnings": [], "slices": [
                        {"source_id": "src_01", "start": 1.0, "end": 5.0, "at": 0.0, "duration": 4.0, "tier": 0},
                        {"source_id": "src_02", "start": 2.0, "end": 6.0, "at": 4.0, "duration": 4.0, "tier": 1, "reused": True,
                         "variation": "alt_crop"}]},
                   {"scene_id": "sc_002", "start": 8.0, "end": 12.0, "warnings": [], "slices": [
                        {"source_id": "src_01", "start": 10.0, "end": 14.0, "at": 8.0, "duration": 4.0, "tier": 0, "kind": "map", "ref": "g1"}]}],
        "warnings": []})


def _saved(run: Path) -> dict:
    return json.loads((run / "visual_plan.json").read_text(encoding="utf-8"))


class SliceEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.run = make_run(Path(self.temp.name))
        _plan(self.run)

    def tearDown(self):
        self.temp.cleanup()

    def test_trim_swap_and_cover(self):
        cover = {"band": {"top": 0.8, "bottom": 0.95}, "logos": [[0.02, 0.02, 0.2, 0.1]]}
        # Boundary moved to 3 s, the two footages swapped, the second cut with its own cover.
        result = visual.edit_slices(self.run, {"scenes": [{"scene_id": "sc_001", "slices": [
            {"from": ["sc_001", 1], "source_id": "src_02", "start": 2.0, "at": 0.0},
            {"from": ["sc_001", 0], "source_id": "src_01", "start": 1.0, "at": 3.0, "cover": cover}]}]})
        self.assertEqual(result, {"scenes": 1, "slices": 3})
        first, second = _saved(self.run)["scenes"][0]["slices"]
        # Still the planned footage: the repeat keeps its other crop.
        self.assertEqual(first, {"source_id": "src_02", "start": 2.0, "end": 5.0, "at": 0.0, "duration": 3.0, "tier": 1, "reused": True,
                                 "variation": "alt_crop"})
        self.assertEqual((second["start"], second["end"], second["duration"], second["tier"]), (1.0, 6.0, 5.0, 0))
        self.assertEqual(second["cover"], {"band": {"top": 0.8, "bottom": 0.95, "share": 1.0}, "logos": [[0.02, 0.02, 0.2, 0.1]]})
        # The other scene is untouched.
        self.assertEqual(_saved(self.run)["scenes"][1]["slices"][0]["kind"], "map")

    def test_replace_and_remove(self):
        # One cut fills the scene: other footage, which grew past the source's end so it starts earlier.
        visual.edit_slices(self.run, {"scenes": [{"scene_id": "sc_001", "slices": [
            {"from": ["sc_001", 1], "source_id": "src_02", "start": 4.0, "at": 0.0}]}]})
        (piece,) = _saved(self.run)["scenes"][0]["slices"]
        self.assertEqual(piece, {"source_id": "src_02", "start": 1.0, "end": 9.0, "at": 0.0, "duration": 8.0, "tier": -1})
        # A generated picture replaced by footage is footage.
        visual.edit_slices(self.run, {"scenes": [{"scene_id": "sc_002", "slices": [
            {"from": ["sc_002", 0], "source_id": "src_01", "start": 3.0, "at": 8.0}]}]})
        self.assertNotIn("kind", _saved(self.run)["scenes"][1]["slices"][0])

    def test_bad_edits_are_refused(self):
        good = {"from": None, "source_id": "src_01", "start": 1.0, "at": 0.0}
        for scenes in ([{"scene_id": "sc_009", "slices": [good]}],
                       [{"scene_id": "sc_001", "slices": []}],
                       [{"scene_id": "sc_001", "slices": [{**good, "at": 1.0}]}],
                       [{"scene_id": "sc_001", "slices": [good, {**good, "at": 7.8}]}],
                       [{"scene_id": "sc_001", "slices": [good, {**good, "at": 0.2}]}],
                       [{"scene_id": "sc_001", "slices": [{**good, "source_id": "src_09"}]}],
                       [{"scene_id": "sc_001", "slices": [{**good, "start": -1}]}],
                       [{"scene_id": "sc_001", "slices": [{**good, "cover": {"band": {"top": 0.9, "bottom": 0.5}}}]}],
                       [{"scene_id": "sc_001", "slices": [good]}, {"scene_id": "sc_001", "slices": [good]}]):
            with self.subTest(scenes), self.assertRaises(DocuError):
                visual.edit_slices(self.run, {"scenes": scenes})
        with self.assertRaises(DocuError):
            visual.edit_slices(self.run, {"scenes": []})
        # Footage shorter than the cut.
        plan = _saved(self.run)
        plan["sources"][1]["duration"] = 5.0
        write_json(self.run / "visual_plan.json", plan)
        with self.assertRaises(DocuError):
            visual.edit_slices(self.run, {"scenes": [{"scene_id": "sc_001", "slices": [{**good, "source_id": "src_02"}]}]})

    def test_a_new_plan_keeps_the_hand_cut_scene(self):
        cover = {"band": None, "logos": [[0.7, 0.05, 0.2, 0.1]]}
        visual.edit_slices(self.run, {"scenes": [{"scene_id": "sc_001", "slices": [
            {"from": ["sc_001", 0], "source_id": "src_01", "start": 1.0, "at": 0.0, "cover": cover},
            {"from": ["sc_001", 1], "source_id": "src_02", "start": 2.0, "at": 6.0}]}]})
        edited = _saved(self.run)["scenes"][0]["slices"]
        fresh = _saved(self.run)
        _plan(self.run)
        fresh = _saved(self.run)
        self.assertEqual(visual.apply_overrides(self.run, fresh), [])
        self.assertEqual(fresh["scenes"][0]["slices"], edited)
        # Another narration timing for the scene: the cut is dropped, and said.
        _plan(self.run)
        moved = _saved(self.run)
        moved["scenes"][0]["end"] = 7.0
        self.assertIn("không còn chỗ", visual.apply_overrides(self.run, moved)[0])
        # Footage picked later over MCP goes into the hand cut.
        visual.save_override(self.run, "s", "sc_001", 1, "src_01", 12.0)
        saved = json.loads((work_dir(self.run) / visual.OVERRIDES).read_text(encoding="utf-8"))
        self.assertEqual(saved["scenes"]["sc_001"]["slices"][1]["start"], 12.0)
        self.assertEqual(saved["slices"], {})

    def test_the_render_frames_a_cut_with_its_own_cover(self):
        saved = {"band": None, "zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True, "logos": []}
        self.assertIs(visual.cover_framing(saved, None, "none"), saved)
        framing = visual.cover_framing(saved, {"band": {"top": 0.82, "bottom": 0.95, "share": 1.0}, "logos": [[0.4, 0.4, 0.2, 0.2]]}, "none")
        self.assertEqual(framing["band"]["top"], 0.82)
        self.assertEqual(framing["logos"], [[0.4, 0.4, 0.2, 0.2]])
        plain = roughcut.video_filter(1920, 1080, saved, frames=60, ken_burns=False)
        covered = roughcut.video_filter(1920, 1080, framing, frames=60, ken_burns=False)
        self.assertNotEqual(plain, covered)
        self.assertIn("gblur", covered)

    def test_cli(self):
        request = self.run / "request.json"
        write_json(request, {"scenes": [{"scene_id": "sc_002", "slices": [{"from": ["sc_002", 0], "source_id": "src_01", "start": 10.0, "at": 8.0}]}]})
        done = subprocess.run([sys.executable, "-m", "engine.dubflow.docu", "slices", "--run-dir", str(self.run), "--request", str(request)],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])["state"], "COMPLETED", done.stdout + done.stderr)
        self.assertEqual(_saved(self.run)["scenes"][1]["slices"][0]["kind"], "map")


if __name__ == "__main__":
    unittest.main()

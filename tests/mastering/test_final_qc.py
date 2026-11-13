"""Final QC before an Assisted video is finalised: a successful render is not a passed QC."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import final_qc as qc
from engine.dubflow.mastering import review as rv

from tests.mastering.test_review_queue import world


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "mastering" / "valid" / "product-plan.json"


def plan_dict(segments, duration=6000, source_ms=10_000):
    """The contract's valid product plan, with one video source and the given segments (milliseconds)."""
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = dict(base["sources"][0], duration=str(source_ms))
    template = base["segments"][0]
    out = dict(base, duration=str(duration), sources=[source], warnings=[])
    out["segments"] = [dict(template, segment_id=f"s{i}", source_id=source["source_id"], role=role, speech_refs=[ref],
                            source={"start": str(a), "end": str(b)}, timeline={"start": str(t0), "end": str(t1)},
                            framing=None, effects=[], reasons=[])
                       for i, (ref, a, b, t0, t1, role) in enumerate(segments)]
    return out


GOOD = [("u1", 0, 2000, 0, 2000, "primary"), ("u2", 2000, 4000, 2000, 4000, "primary"), ("u3", 4000, 6000, 4000, 6000, "primary")]


def settled_world():
    made = world()
    review = rv.Review()
    for unit in made["units"]:
        review.decide(made, unit["unit_id"], "approve")
    return made, review


class PartTests(unittest.TestCase):
    def checks(self, made, plan, review):
        return {c["id"]: c for c in qc.check_parts([("video 1", made, plan, review)])}

    def test_a_reviewed_valid_plan_passes_every_content_check(self):
        made, review = settled_world()
        checks = self.checks(made, plan_dict(GOOD), review)
        self.assertTrue(all(c["ok"] for c in checks.values()), checks)

    def test_a_segment_past_the_source_end_or_a_gap_fails(self):
        made, review = settled_world()
        past = plan_dict([("u1", 0, 2000, 0, 2000, "primary"), ("u2", 9000, 11_000, 2000, 4000, "primary"),
                          ("u3", 4000, 6000, 4000, 6000, "primary")])
        self.assertFalse(self.checks(made, past, review)["timeline"]["ok"])
        gap = plan_dict([("u1", 0, 2000, 0, 2000, "primary"), ("u3", 4000, 6000, 4000, 6000, "primary")])
        self.assertFalse(self.checks(made, gap, review)["timeline"]["ok"])

    def test_a_frozen_video_frame_fails(self):
        made, review = settled_world()
        held = plan_dict([("u1", 0, 2000, 0, 2000, "primary"), ("u2", 1999, 2000, 2000, 4000, "still"),
                          ("u3", 4000, 6000, 4000, 6000, "primary")])
        self.assertFalse(self.checks(made, held, review)["timeline"]["ok"])

    def test_a_pin_not_rendered_yet_is_pending(self):
        made, review = settled_world()
        review.decide(made, "u3", "pin", ["c3"])
        check = self.checks(made, plan_dict(GOOD), review)["pending"]
        self.assertFalse(check["ok"])
        self.assertIn("câu 3", check["detail"])
        made["edits"]["pin"]["u3"] = ["c3"]  # the render that applied it
        self.assertTrue(self.checks(made, plan_dict(GOOD), review)["pending"]["ok"])

    def test_an_exclusion_taken_back_after_the_render_is_pending(self):  # #805
        made, review = settled_world()
        shot = made["candidates"][-1]["id"]
        review.decide(made, "u3", "exclude", [shot])
        made["edits"]["exclude"] = [shot]  # the render that applied it
        self.assertTrue(self.checks(made, plan_dict(GOOD), review)["pending"]["ok"])
        review.decide(made, "u3", "include", [shot])
        check = self.checks(made, plan_dict(GOOD), review)["pending"]
        self.assertFalse(check["ok"])
        self.assertIn("câu 3", check["detail"])

    def test_an_exclusion_taken_back_by_undo_or_reset_is_pending(self):  # #805 review
        for how in ("undo", "reset"):
            made, review = settled_world()
            shot = made["candidates"][-1]["id"]
            review.decide(made, "u3", "exclude", [shot])
            made["edits"]["exclude"] = [shot]  # the render that applied it
            self.assertTrue(self.checks(made, plan_dict(GOOD), review)["pending"]["ok"])
            if how == "undo":
                review.undo(made)
            else:
                review.decide(made, "u3", "reset")
            self.assertFalse(self.checks(made, plan_dict(GOOD), review)["pending"]["ok"], how)

    def test_footage_the_video_shows_that_is_now_excluded_is_pending(self):  # #805 review
        from tests.mastering.test_review import footage
        made = world()
        made["candidates"] = [footage(c["id"], 2000 * n, 2000 * (n + 1)) for n, c in enumerate(made["candidates"])]
        review = rv.Review()
        for unit in made["units"]:
            review.decide(made, unit["unit_id"], "approve")
        shown = made["units"][0]["chosen"][0]
        other = next(c["id"] for c in made["candidates"] if c["id"] != shown)
        review.decide(made, "u3", "exclude", [other])
        made["edits"]["exclude"] = [other]  # rendered with that exclusion...
        self.assertTrue(self.checks(made, plan_dict(GOOD), review)["pending"]["ok"])
        # ...then the same id is excluded again where it now names the footage the video shows
        source = next(c for c in made["candidates"] if c["id"] == shown)
        review.units["u3"].ranges[other].append([source["source_id"], source["start_ms"], source["end_ms"]])
        check = self.checks(made, plan_dict(GOOD), review)["pending"]
        self.assertFalse(check["ok"])
        self.assertIn("câu 3", check["detail"])

    def test_unreviewed_needs_acceptance_and_critical_is_never_waved_through(self):
        made = world()
        review = rv.Review()
        checks = self.checks(made, plan_dict(GOOD), review)
        self.assertFalse(checks["decisions"]["ok"])
        self.assertFalse(checks["claims"]["ok"])
        review.set_accept_unreviewed(True)
        checks = self.checks(made, plan_dict(GOOD), review)
        self.assertTrue(checks["decisions"]["ok"])
        self.assertIn("chấp nhận", checks["decisions"]["detail"])
        self.assertFalse(checks["claims"]["ok"])  # accepting "unreviewed" does not settle a critical sentence
        self.assertIn("câu 2", checks["claims"]["detail"])
        review.decide(made, "u2", "missing")
        review.decide(made, "u4", "approve")
        self.assertTrue(self.checks(made, plan_dict(GOOD), review)["claims"]["ok"])


class OutputTests(unittest.TestCase):
    def test_length_captions_and_peak(self):
        with TemporaryDirectory() as tmp:
            video = Path(tmp) / "video-1.mp4"
            video.write_bytes(b"x")
            captions = Path(tmp) / "captions-1.srt"
            captions.write_text("1\n00:00:00,000 --> 00:00:05,900\nxin chào\n", encoding="utf-8")
            good = {c["id"]: c for c in qc.check_output(video, 6000, captions, probe=lambda _p: 6040, peak=lambda _p: -1.8)}
            self.assertTrue(all(c["ok"] for c in good.values()), good)
            bad = {c["id"]: c for c in qc.check_output(video, 6000, captions, probe=lambda _p: 6500, peak=lambda _p: 0.4)}
            self.assertFalse(bad["duration"]["ok"])
            self.assertFalse(bad["audio"]["ok"])
            captions.write_text("1\n00:00:05,000 --> 00:00:07,000\nquá dài\n", encoding="utf-8")
            late = {c["id"]: c for c in qc.check_output(video, 6000, captions, probe=lambda _p: 6000)}
            self.assertFalse(late["captions"]["ok"])
            self.assertIsNone(late["audio"]["ok"])  # not measured is said, never counted as passed

    def test_no_output_fails(self):
        self.assertFalse(qc.check_output(None, 6000, None)[0]["ok"])


class StateTests(unittest.TestCase):
    def test_states(self):
        ok = [qc._check("render", "", True), qc._check("pending", "", True), qc._check("decisions", "", True)]
        self.assertEqual(qc.result(ok, "COMPLETED")["state"], "READY_TO_FINALIZE")
        self.assertEqual(qc.result(ok, "RUNNING")["state"], "RENDERING")
        self.assertEqual(qc.result(ok, "FAILED")["state"], "FINAL_QC_FAILED")  # a failed render is never ready
        self.assertEqual(qc.result([*ok[:1], qc._check("pending", "", False), ok[2]], "COMPLETED")["state"], "RENDER_PENDING")
        self.assertEqual(qc.result([*ok[:2], qc._check("decisions", "", False)], "COMPLETED")["state"], "REVIEW_PENDING")
        self.assertEqual(qc.result([*ok, qc._check("audio", "", False)], "COMPLETED")["state"], "FINAL_QC_FAILED")
        unmeasured = qc.result([*ok, qc._check("audio", "", None)], "COMPLETED")
        self.assertEqual(unmeasured["state"], "READY_TO_FINALIZE")
        self.assertEqual(unmeasured["unmeasured"], ["audio"])

    def test_product_run_end_to_end_from_files(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp) / "run-1"
            work = run / ".dubflow-work"
            work.mkdir(parents=True)
            made, review = settled_world()
            (work / "editorial-1.json").write_text(json.dumps(made, ensure_ascii=False), encoding="utf-8")
            (work / "mastering_plan-1.json").write_text(json.dumps(plan_dict(GOOD)), encoding="utf-8")
            rv.save(rv.path_for(work, 1), review)
            (run / "video-1.mp4").write_bytes(b"x")
            (run / "captions-1.srt").write_text("1\n00:00:00,000 --> 00:00:05,500\nx\n", encoding="utf-8")
            (work / "status.json").write_text(json.dumps({"state": "COMPLETED", "videos": [
                {"index": 1, "ok": True, "path": str(run / "video-1.mp4"), "duration_ms": 6000}]}), encoding="utf-8")
            out = qc.product_qc(work, 1, probe=lambda _p: 6000, peak=lambda _p: -2.0)
            self.assertEqual(out["state"], "READY_TO_FINALIZE", out)


if __name__ == "__main__":
    unittest.main()

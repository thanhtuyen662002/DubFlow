"""Final QC before an Assisted video is finalised: a successful render is not a passed QC."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import final_qc as qc
from engine.dubflow.mastering import review as rv

from tests.mastering.test_review_queue import POPULATION_CLAIM, with_claim, world
from tests.mastering.test_review import cand, log, unit


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


def claim_world():
    """world() with the opening and the unfound sentence settled, u1 READY, and u3 a displayed claim not shown (#817)."""
    made = with_claim(world(), "u3", POPULATION_CLAIM)
    review = rv.Review()
    review.decide(made, "u2", "missing")
    review.decide(made, "u4", "approve")
    return made, review


class ClaimTests(unittest.TestCase):  # #817
    def checks(self, parts):
        return {c["id"]: c for c in qc.check_parts(parts)}

    def test_accepting_the_unreviewed_does_not_wave_an_unverified_claim_through(self):
        made, review = claim_world()
        review.set_accept_unreviewed(True)
        checks = self.checks([("video 1", made, plan_dict(GOOD), review)])
        self.assertTrue(checks["decisions"]["ok"])  # the blanket acceptance is the user's ...
        self.assertFalse(checks["claims"]["ok"])    # ... but a critical claim is never settled by it
        self.assertIn("câu 3", checks["claims"]["detail"])
        out = qc.result([qc._check("render", "", True), *checks.values()], "COMPLETED")
        self.assertEqual(out["state"], "FINAL_QC_FAILED")
        self.assertFalse(out["passed"])

    def test_a_contradicted_claim_is_listed(self):
        for claims in ({"display": True, "verified": [], "unverified": [], "conflicts": ["1.500 dặm"]},
                       {"display": True, "verified": ["1.500 dặm"], "unverified": [], "conflicts": ["1.500 dặm"]},
                       dict(POPULATION_CLAIM, disputed=["193.000.000 người"])):
            made = with_claim(world(), "u3", claims, note="cảnh hiển thị con số khác với lời câu (1.500 dặm): cần kiểm tra")
            review = rv.Review()
            review.decide(made, "u2", "missing")
            review.decide(made, "u4", "approve")
            review.set_accept_unreviewed(True)
            check = self.checks([("video 1", made, plan_dict(GOOD), review)])["claims"]
            self.assertFalse(check["ok"], claims)
            self.assertIn("câu 3", check["detail"])

    def test_a_verified_claim_with_low_coverage_is_not_on_the_claims_line(self):  # rc16 i16.u1
        made = world()
        u3 = made["units"][2]
        u3["notes"] = ["độ phủ 81 %"]
        u3["signals"] = {"claims": {"display": True, "verified": ["1.500 dặm"], "unverified": [], "conflicts": []}}
        rv.annotate(made)
        review = rv.Review()
        review.decide(made, "u2", "missing")
        review.decide(made, "u4", "approve")
        check = self.checks([("video 1", made, plan_dict(GOOD), review)])["claims"]
        self.assertTrue(check["ok"], check)

    def test_an_approved_claim_passes_as_the_users_acceptance(self):
        for how, shots in (("approve", None), ("pin", ["c2"]), ("missing", None)):
            made, review = claim_world()
            review.decide(made, "u3", how, shots)
            if how == "pin":
                made["edits"]["pin"]["u3"] = ["c2"]  # rendered with it
            checks = self.checks([("video 1", made, plan_dict(GOOD), review)])
            self.assertTrue(checks["claims"]["ok"], (how, checks["claims"]))
            self.assertTrue(checks["decisions"]["ok"], how)
            self.assertIn("người dùng đã chấp nhận: câu 3", checks["claims"]["detail"])
            shown = {u["unit_id"]: u for u in rv.view(made, None, review)["units"]}["u3"]
            self.assertIsNone(shown["priority"])  # not asked again
            self.assertEqual(shown["claims"][0]["status"], "UNVERIFIED")  # the user's word, not a confirmation

    def test_an_invalidated_approval_is_listed_again(self):
        made, review = claim_world()
        review.decide(made, "u3", "approve")
        made["units"][2]["chosen"] = ["c3"]
        review.reconcile(made)
        review.set_accept_unreviewed(True)
        self.assertFalse(self.checks([("video 1", made, plan_dict(GOOD), review)])["claims"]["ok"])

    def test_an_accept_from_an_earlier_run_does_not_wave_a_new_claim_through(self):
        # the part was accepted, then planned again with the same shots: the render is reused, so the acceptance stays
        # (raw.py resets it only on a re-render), and the new plan finds a displayed claim unshown
        made = world()
        review = rv.Review()
        review.decide(made, "u2", "missing")
        review.decide(made, "u4", "approve")
        review.set_accept_unreviewed(True)
        self.assertTrue(self.checks([("video 1", made, plan_dict(GOOD), review)])["claims"]["ok"])
        again = with_claim(world(), "u3", POPULATION_CLAIM)
        review.reconcile(again)
        self.assertFalse(self.checks([("video 1", again, plan_dict(GOOD), review)])["claims"]["ok"])

    def test_claims_are_listed_across_parts(self):
        first, one = claim_world()
        second, two = claim_world()
        check = self.checks([("i1", first, plan_dict(GOOD), one), ("i16", second, plan_dict(GOOD), two)])["claims"]
        self.assertFalse(check["ok"])
        self.assertIn("i1: câu 3", check["detail"])
        self.assertIn("i16: câu 3", check["detail"])
        self.assertIn("2 câu", check["detail"])

    def test_every_critical_sentence_is_counted_and_none_is_dropped(self):  # rc16 W3: 15 critical, 12 shown, no "+3"
        units = []
        for n in range(1, 16):
            u = unit(f"u{n}", matched=1, status="partial", needs_review=True, chosen=("c1",))
            u["signals"] = {"claims": dict(POPULATION_CLAIM)}
            units.append(u)
        made = log(units, [cand("c1", text="biểu đồ"), cand("c2", text="cánh đồng")])
        rv.annotate(made)
        review = rv.Review()
        review.set_accept_unreviewed(True)
        segments = [(f"u{n}", 0, 400, (n - 1) * 400, n * 400, "primary") for n in range(1, 16)]
        check = self.checks([("video 1", made, plan_dict(segments, duration=6000), review)])["claims"]
        self.assertFalse(check["ok"])
        self.assertTrue(check["detail"].startswith("15 câu chưa xử lý"), check["detail"])
        listed = check["detail"].split(": ", 1)[1].removesuffix(" và 3 câu khác").split(", ")
        self.assertEqual(listed, [f"câu {n}" for n in range(1, 13)])
        self.assertNotIn("câu 13", check["detail"])
        self.assertTrue(check["detail"].endswith("câu 12 và 3 câu khác"), check["detail"])
        self.assertLessEqual(len(check["detail"]), 400)  # what the desktop shows of a detail

    def test_twelve_are_listed_without_a_tail(self):
        names = [f"câu {n}" for n in range(1, 13)]
        self.assertEqual(qc.listed(names), ", ".join(names))
        self.assertEqual(qc.listed([*names, "câu 13"]), ", ".join(names) + " và 1 câu khác")


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

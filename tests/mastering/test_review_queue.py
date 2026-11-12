"""Assisted Editorial Production §B: the review queue, the Human Decision Contract and "no suitable footage".

The queue groups (critical, review, optional) say what the planner and the checks flagged; they are not a measured
risk, and the view says so (``queue_calibrated: false``).
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import review as rv

from tests.mastering.test_review import cand, log, unit


def world():
    units = [
        unit("u1", matched=2, status="full"),                                  # READY
        unit("u2", matched=0, status="none", needs_review=True),               # no match: critical
        unit("u3", matched=1, status="partial", needs_review=True),            # NEEDS_REVIEW
        unit("u4", matched=0, status="generic", needs_review=True, function="hook"),  # an opening flagged (P1-N1)
    ]
    units[3]["notes"] = ["câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm trong cảnh được chọn: cần kiểm tra"]
    made = log(units, [cand("c1", text="máy pha cà phê"), cand("c2", text="tách cà phê"), cand("c3", text="bếp")])
    rv.annotate(made)
    return made


class QueueTests(unittest.TestCase):
    def test_groups_order_and_reasons(self):
        made = world()
        shown = rv.view(made, None, rv.Review())
        by = {u["unit_id"]: u for u in shown["units"]}
        self.assertEqual(by["u2"]["priority"], rv.CRITICAL)
        self.assertEqual(by["u4"]["priority"], rv.CRITICAL)  # a claim with no visual evidence is never just "review"
        self.assertIn("câu nêu thông tin cụ thể nhưng không có bằng chứng hình", by["u4"]["queue_reasons"])
        self.assertEqual(by["u3"]["priority"], rv.REVIEW)
        self.assertEqual(by["u1"]["priority"], rv.OPTIONAL)
        self.assertEqual(shown["queue"], ["u2", "u4", "u3", "u1"])
        self.assertFalse(shown["queue_calibrated"])

    def test_signals_only_puts_every_sentence_in_critical(self):
        made = world()
        made["mode"] = "signals_only"
        shown = rv.view(made, None, rv.Review())
        self.assertTrue(all(u["priority"] == rv.CRITICAL for u in shown["units"]))

    def test_a_number_the_product_facts_lack_is_critical(self):
        made = world()
        shown = rv.view(made, None, rv.Review(), warnings=["Câu 1 nêu 3000W nhưng thông tin sản phẩm không có: hãy kiểm tra trước khi đăng."])
        self.assertEqual({u["unit_id"]: u["priority"] for u in shown["units"]}["u1"], rv.CRITICAL)

    def test_settled_sentences_leave_the_queue_and_later_goes_last_in_its_group(self):
        made = world()
        review = rv.Review()
        review.decide(made, "u1", "approve")
        review.decide(made, "u2", "later")
        shown = rv.view(made, None, review)
        self.assertEqual(shown["queue"], ["u4", "u2", "u3"])  # u2 still critical, after the other critical one
        self.assertIn("u2", shown["unreviewed"])  # put off is not reviewed
        self.assertEqual(shown["position"], "u2")

    def test_an_invalidated_decision_is_critical(self):
        made = world()
        review = rv.Review()
        review.decide(made, "u1", "approve")
        made["units"][0]["chosen"] = ["c3"]  # the footage changed after the approval
        review.reconcile(made)
        shown = rv.view(made, None, review)
        u1 = {u["unit_id"]: u for u in shown["units"]}["u1"]
        self.assertEqual(u1["priority"], rv.CRITICAL)
        self.assertEqual(review.records[-1]["type"], "INVALIDATED")
        self.assertTrue(review.records[-1]["invalidation_reason"])

    def test_an_invalidation_names_its_run(self):  # rc8 P3: INVALIDATED records had an empty run_id
        made = world()
        review = rv.Review()
        review.decide(made, "u1", "approve", run="run-3")
        made["units"][0]["chosen"] = ["c3"]
        review.reconcile(made, run="run-3")
        self.assertEqual((review.records[-1]["type"], review.records[-1]["run_id"]), ("INVALIDATED", "run-3"))

    def test_what_the_user_settled_is_counted_as_theirs(self):  # rc8 P1-N3
        made = world()
        review = rv.Review()
        self.assertEqual(rv.settled_by_user(made, review), [])
        review.decide(made, "u2", "missing")
        review.decide(made, "u3", "pin", ["c2"])
        review.decide(made, "u4", "approve")
        review.decide(made, "u1", "approve")  # READY already: the planner's pass, not counted as the user's
        self.assertEqual(rv.unreviewed(made, review), [])
        self.assertEqual(sorted(rv.settled_by_user(made, review)), ["u2", "u3", "u4"])


class SourceMissingTests(unittest.TestCase):
    def test_no_suitable_footage_settles_the_sentence_without_changing_the_verdict(self):
        made = world()
        review = rv.Review()
        before = {u["unit_id"]: u for u in rv.view(made, None, review)["units"]}["u2"]["state"]
        self.assertIn(before, (rv.MATCH_NOT_FOUND, rv.SOURCE_INSUFFICIENT))
        review.decide(made, "u2", "missing")
        shown = rv.view(made, None, review)
        u2 = {u["unit_id"]: u for u in shown["units"]}["u2"]
        self.assertEqual(u2["state"], before)  # the planner's word stays
        self.assertEqual(u2["user_state"], rv.USER_SOURCE_MISSING)
        self.assertIsNone(u2["priority"])
        self.assertNotIn("u2", shown["unreviewed"])
        self.assertEqual(review.edits().to_dict()["pin"], {})  # nothing reaches the planner
        review.decide(made, "u2", "missing")  # the same button takes it back
        self.assertIn("u2", rv.view(made, None, review)["unreviewed"])

    def test_choosing_a_shot_clears_later_and_missing(self):
        made = world()
        review = rv.Review()
        review.decide(made, "u2", "missing")
        review.decide(made, "u2", "pin", ["c3"])
        self.assertFalse(review.units["u2"].source_missing)
        self.assertEqual(review.records[-1]["type"], "REPLACE")  # a shot the sentence did not show


class RecordTests(unittest.TestCase):
    def test_each_decision_is_recorded_with_the_contract_fields(self):
        made = world()
        review = rv.Review()
        review.decide(made, "u1", "pin", ["c1"], run="run-7", now="2026-11-12T03:00:00Z")
        entry = review.records[-1]
        self.assertEqual(entry["type"], "PIN")  # the shot already shown
        for key in ("run_id", "plan_revision", "speech_ref", "source_id", "shot_id", "source_ms", "type", "decision_revision",
                    "approval_state", "input_fingerprint", "decided_at", "invalidation_reason"):
            self.assertIn(key, entry)
        self.assertEqual((entry["run_id"], entry["plan_revision"], entry["speech_ref"], entry["shot_id"]),
                         ("run-7", "product-1", "u1", "c1"))
        self.assertEqual(entry["source_ms"], [0, 2000])
        self.assertEqual(entry["decision_revision"], review.revision)
        review.decide(made, "u1", "approve", now="2026-11-12T03:01:00Z")
        self.assertEqual(review.records[-1]["approval_state"], rv.USER_APPROVED)
        review.decide(made, "u3", "exclude", ["c2"])
        self.assertEqual(review.records[-1]["type"], "EXCLUDE")
        self.assertTrue(review.undo(made))
        self.assertEqual(review.records[-1]["type"], "UNDO")
        self.assertEqual({r["type"] for r in review.records} <= set(rv.DECISION_TYPES), True)

    def test_the_fingerprint_follows_what_the_sentence_showed(self):
        made = world()
        candidates = {c["id"]: c for c in made["candidates"]}
        first = rv.fingerprint(made["units"][0], candidates)
        made["units"][0]["chosen"] = ["c2"]
        self.assertNotEqual(rv.fingerprint(made["units"][0], candidates), first)

    def test_focus_keeps_the_place_without_a_revision(self):
        made = world()
        review = rv.Review()
        review.decide(made, "u3", "focus")
        self.assertEqual((review.position, review.revision, review.records, review.history), ("u3", 0, [], []))

    def test_version_1_files_still_read_and_version_2_round_trips(self):
        old = {"schema_version": 1, "kind": "editorial_review", "plan_id": "product-1", "revision": 2,
               "units": {"u1": {"pin": ["c1"], "exclude": []}}, "acknowledged": [], "accept_unreviewed": False, "history": []}
        loaded = rv.Review.from_dict(old)
        self.assertEqual(loaded.units["u1"].pin, ["c1"])
        made = world()
        loaded.decide(made, "u2", "later")
        again = rv.Review.from_dict(json.loads(json.dumps(loaded.to_dict())))
        self.assertEqual(again.to_dict(), loaded.to_dict())
        self.assertEqual(again.to_dict()["schema_version"], 2)
        bad = loaded.to_dict()
        bad["records"] = [{"type": "DELETE_EVERYTHING"}]
        with self.assertRaises(rv.ReviewError):
            rv.Review.from_dict(bad)

    def test_every_usable_shot_is_offered(self):
        made = world()
        made["candidates"].append(cand("c4", text="mờ", problems=["mờ"]))
        self.assertEqual([s["id"] for s in rv.view(made, None, rv.Review())["shots"]], ["c1", "c2", "c3"])


class CliTests(unittest.TestCase):
    def test_the_new_actions_through_the_desktop_call(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp) / "run-9" / ".dubflow-work"
            work.mkdir(parents=True)
            (work / "editorial-1.json").write_text(json.dumps(world(), ensure_ascii=False), encoding="utf-8")
            for args in (["decide", "u2", "missing"], ["decide", "u3", "later"], ["decide", "u1", "focus"]):
                with redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(rv.main(["--work", str(work), "--video", "1", *args]), 0)
            shown = json.loads(out.getvalue())
            self.assertEqual(shown["position"], "u1")
            saved = json.loads((work / "review-1.json").read_text(encoding="utf-8"))
            self.assertEqual([r["type"] for r in saved["records"]], ["MARK_SOURCE_MISSING", "REVIEW_LATER"])
            self.assertEqual(saved["records"][0]["run_id"], "run-9")  # the folder name, never a full path


if __name__ == "__main__":
    unittest.main()

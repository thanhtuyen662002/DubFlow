"""#843: accepting the unreviewed sentences in bulk never exports a sentence whose timeline mostly shows something else
than what it says is displayed.

rc16 W3 ``i1.u2`` after #837: 960 ms of the title card, then 2730 ms (73 %) of a quotation card that shows none of
"The Battle of Russia". #837 made it NEEDS_REVIEW, but in the queue it was a plain doubt (``review``), so "Tôi chấp
nhận xuất video với các câu chưa duyệt" exported it and Final QC said "không còn mục nghiêm trọng". It is now
``critical`` when the pieces that show none of the displayed claims fill more than half of the sentence (the
evaluation contract's majority_wrong_duration boundary): a person must settle it one by one; bulk acceptance does not.
A short such piece (a cut, a montage) stays a doubt that may be accepted. A sentence the user settles by hand passes and
is named as the user's acceptance. Pins, exclusions and decisions are kept.

Synthetic shots and files only: no media, no network, no AI.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import align, evidence as ev, candidates as shots, final_qc as qc, policy as rules, review as rv
from tests.mastering.test_final_qc import plan_dict

PACING = rules.STYLE_PACING["natural"]
TEXT = "Dòng chữ lớn The Battle of Russia hiện lên trên nền huy hiệu đại bàng."
TITLE = ("Màn hình tiêu đề đen trắng với chữ lớn chồng lên hình huy hiệu đại bàng mờ phía sau. chữ tiêu đề, huy hiệu đại "
         "bàng THE BATTLE OF RUSSIA; WHY WE FIGHT; A SERIES OF SEVEN INFORMATION FILMS; PART 1")
QUOTE = ("Màn chữ đen trắng trên nền huy hiệu đại bàng, trích dẫn lời Henry L. Stimson, Bộ trưởng Chiến tranh. huy hiệu "
         "đại bàng, chữ Henry L. Stimson Secretary of War")
SKY = "Bầu trời có mây"


def _candidate(cid: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, "v1", start, end, 0, 50, shots.Quality(120, 50, 300, ()), random.Random(cid).getrandbits(64))


def _video(title_ms: int, quote_ms: int):
    """One sentence, two pieces: the title card for ``title_ms``, then the quotation card for ``quote_ms``."""
    length = title_ms + quote_ms
    seen = {"c1": (0, title_ms, TITLE, ("text_on_screen", "establishing")),
            "c2": (title_ms, length, QUOTE, ("text_on_screen", "establishing")),
            "c3": (length, length + 20_000, SKY, ("establishing",))}
    pool = [_candidate(k, a, b) for k, (a, b, _, _) in seen.items()]
    found = ev.gather(seen)
    for key, (_, _, text, labels) in seen.items():
        found[key].add(ev.Item("vision", 900, text, labels, "AI"))
    unit = align.Unit("u1", TEXT, 0, length, "narration")
    choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=True)
    mine = [c for c in choices if c.unit_id == "u1"]
    candidates = [{"id": k, "source_id": "v1", "start_ms": a, "end_ms": b, "evidence": found[k].to_dict()}
                  for k, (a, b, _, _) in seen.items()]
    log = {"mode": mode, "plan_id": "p", "candidates": candidates}
    state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, log, [{"needs_review": c.needs_review} for c in mine])
    written = decision.to_dict()
    log["units"] = [written]
    written["review"] = {"state": state, "evidence": rv.evidence_state(written, log)}
    segments, at = [], 0
    for c in mine:
        segments.append(("u1", c.candidate.start_ms, c.candidate.start_ms + c.length_ms, at, at + c.length_ms, "primary"))
        at += c.length_ms
    return log, plan_dict(segments, duration=at, source_ms=200_000), at, state


def _final_qc(log, plan, duration_ms: int, review: rv.Review) -> dict:
    """Final QC of a Bán hàng video from its files, the output measured clean (length, captions, peak)."""
    with TemporaryDirectory() as tmp:
        run = Path(tmp) / "run-1"
        work = run / ".dubflow-work"
        work.mkdir(parents=True)
        (work / "editorial-1.json").write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        (work / "mastering_plan-1.json").write_text(json.dumps(plan), encoding="utf-8")
        rv.save(rv.path_for(work, 1), review)
        (run / "video-1.mp4").write_bytes(b"x")
        (run / "captions-1.srt").write_text("1\n00:00:00,000 --> 00:00:03,000\nx\n", encoding="utf-8")
        (work / "status.json").write_text(json.dumps({"state": "COMPLETED", "videos": [
            {"index": 1, "ok": True, "path": str(run / "video-1.mp4"), "duration_ms": duration_ms}]}), encoding="utf-8")
        out = qc.product_qc(work, 1, probe=lambda _p: duration_ms, peak=lambda _p: -2.0)
        out["_review_after"] = rv.load(rv.path_for(work, 1))
        return out


class BulkAcceptanceNeverHidesAWrongTimeline(unittest.TestCase):
    def test_the_only_problem_of_the_video_blocks_the_export_after_bulk_acceptance(self):
        log, plan, length, state = _video(960, 2730)  # i1.u2: 73 % of the sentence on the quotation card
        self.assertEqual(state, rv.NEEDS_REVIEW)
        review = rv.Review()
        review.set_accept_unreviewed(True)
        out = _final_qc(log, plan, length, review)
        checks = {c["id"]: c for c in out["checks"]}
        # nothing else is wrong with this video ...
        for check in ("render", "timeline", "duration", "captions", "audio", "pending", "decisions"):
            self.assertTrue(checks[check]["ok"], (check, checks[check]))
        # ... and this sentence alone keeps it from being finalised (7810fec: READY_TO_FINALIZE, "không còn mục nghiêm trọng")
        self.assertEqual(out["state"], "FINAL_QC_FAILED", out)
        self.assertFalse(checks["claims"]["ok"])
        self.assertEqual([u["unit_id"] for u in out["critical_units"]], ["u1"])
        self.assertIn("1 câu chưa xử lý", checks["claims"]["detail"])

    def test_the_queue_says_why(self):
        log, plan, _, state = _video(960, 2730)
        rank, why = rv.priority(log["units"][0], state, None, log["mode"])
        self.assertEqual(rank, rv.CRITICAL)
        self.assertTrue(any("73 % thời lượng câu là cảnh không hiển thị" in w for w in why), why)

    def test_without_acceptance_it_waits_for_review(self):
        log, plan, length, _ = _video(960, 2730)
        self.assertEqual(_final_qc(log, plan, length, rv.Review())["state"], "REVIEW_PENDING")

    def test_a_person_may_still_settle_it_and_is_named_for_it(self):
        log, plan, length, _ = _video(960, 2730)
        review = rv.Review()
        review.decide(log, "u1", "approve")
        out = _final_qc(log, plan, length, review)
        self.assertEqual(out["state"], "READY_TO_FINALIZE", out)
        claims_check = next(c for c in out["checks"] if c["id"] == "claims")
        self.assertEqual([u["unit_id"] for u in claims_check["user_accepted"]], ["u1"])
        self.assertIn("người dùng đã chấp nhận", claims_check["detail"])

    def test_a_short_piece_showing_none_of_it_stays_a_doubt_bulk_acceptance_may_take(self):
        log, plan, length, state = _video(2600, 1090)  # 29 % on the quotation card
        self.assertEqual(state, rv.NEEDS_REVIEW)
        rank, _ = rv.priority(log["units"][0], state, None, log["mode"])
        self.assertEqual(rank, rv.REVIEW)  # not every NEEDS_REVIEW is critical
        review = rv.Review()
        review.set_accept_unreviewed(True)
        self.assertEqual(_final_qc(log, plan, length, review)["state"], "READY_TO_FINALIZE")

    def test_exclusions_and_decisions_are_kept_and_still_block(self):
        log, plan, length, _ = _video(960, 2730)
        review = rv.Review()
        review.decide(log, "u1", "exclude", ["c3"])  # a shot the plan does not show
        review.decide(log, "u1", "later")
        log["edits"] = review.edits().to_dict()  # the render already has the exclusion: nothing waits for a re-render
        review.set_accept_unreviewed(True)
        out = _final_qc(log, plan, length, review)
        self.assertEqual(out["state"], "FINAL_QC_FAILED")
        after = out["_review_after"]
        self.assertTrue(after.accept_unreviewed)
        self.assertIn("c3", after.units["u1"].exclude)
        self.assertTrue(after.units["u1"].later)

    def test_a_pin_is_the_users_own_choice_and_settles_it(self):
        log, plan, length, _ = _video(960, 2730)
        review = rv.Review()
        review.decide(log, "u1", "pin", ["c1"])
        rank, _ = rv.priority(log["units"][0], rv.NEEDS_REVIEW, review.units["u1"], log["mode"])
        self.assertIsNone(rank)


if __name__ == "__main__":
    unittest.main()

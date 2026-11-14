"""#837: one evaluation contract for lenient, strict, duration and severe, on the pieces placed on the timeline.

Synthetic units and candidates only (no run folder, no ground-truth file): a unit READY or not, its timeline pieces, a
ground-truth range. ``w3.judge`` is the one implementation; ``w3.score`` and ``w3.attribute`` read it, and a test checks
they agree on the same run.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.mastering.longform import w3
from tests.mastering.longform.test_w3 import make_run, make_truth

TITLE = "Màn hình tiêu đề, chữ THE BATTLE OF RUSSIA trên huy hiệu đại bàng"
QUOTE = "Màn chữ trên nền huy hiệu đại bàng, trích dẫn lời Henry L. Stimson"
SCENE = "Đoàn binh sĩ hành quân trên đường đất"


def _cand(cid: str, start: int, end: int, seen: str = SCENE) -> dict:
    items = [{"origin": "vision", "confidence": 900, "text": seen, "labels": [], "detail": "AI"}] if seen else []
    return {"id": cid, "source_id": "i1-s1", "start_ms": start, "end_ms": end, "evidence": {"candidate_id": cid, "items": items}}


def _unit(pieces: list[tuple[str, int, int]], state: str = "READY", text: str = "Đoàn binh sĩ hành quân.") -> dict:
    return {"unit_id": "i1.u1", "text": text, "chosen": list(dict.fromkeys(p[0] for p in pieces)),
            "pieces": [{"id": i, "offset_ms": o, "length_ms": n} for i, o, n in pieces], "review": {"state": state}}


GT = {"text": "x", "ranges": [[10.0, 20.0]], "kind": "footage"}  # 10 000 – 20 000 ms
CANDS = {"in": _cand("in", 10_000, 20_000), "in2": _cand("in2", 12_000, 19_000), "out": _cand("out", 40_000, 50_000),
         "wide": _cand("wide", 5_000, 30_000), "unseen": _cand("unseen", 40_000, 50_000, seen=""),
         "title": _cand("title", 10_000, 11_000, TITLE), "quote": _cand("quote", 33_000, 40_000, QUOTE)}


def judge(pieces, state="READY", gt=GT, text="Đoàn binh sĩ hành quân."):
    return w3.judge(_unit(pieces, state, text), gt, CANDS)


class Contract(unittest.TestCase):
    def test_all_of_the_timeline_right(self):
        v = judge([("in", 11_000, 3000), ("in2", 14_000, 2000)])
        self.assertEqual((v["lenient"], v["strict"], v["duration_milli"], v["severe"]), (True, True, 1000, False))

    def test_sixty_percent_right_is_not_strict_and_not_severe(self):
        v = judge([("in", 11_000, 3000), ("out", 41_000, 2000)])
        self.assertEqual((v["lenient"], v["strict"], v["duration_milli"], v["severe"]), (True, False, 600, False))

    def test_i1_u2_twenty_six_percent_right_is_severe(self):
        # rc16 i1.u2: 960 ms of the title (inside the range), 2730 ms of a quotation card 13 s after it
        v = judge([("title", 10_000, 960), ("quote", 33_000, 2730)], text="Dòng chữ lớn The Battle of Russia hiện lên.")
        self.assertEqual((v["lenient"], v["strict"], v["duration_milli"]), (True, False, 260))
        self.assertTrue(v["severe"])
        self.assertEqual(v["severe_reasons"], ["majority_wrong_duration"])
        self.assertFalse(v["claim_failure"])  # the title was shown, by the first piece: the duration rule catches it

    def test_nothing_right_is_severe(self):
        v = judge([("out", 41_000, 3000)])
        self.assertEqual((v["lenient"], v["duration_milli"], v["severe_reasons"]), (False, 0, ["no_piece_hits"]))

    def test_the_same_units_not_ready_are_never_severe(self):
        for pieces in ([("out", 41_000, 3000)], [("in", 11_000, 1000), ("out", 41_000, 3000)]):
            self.assertFalse(judge(pieces, state="NEEDS_REVIEW")["severe"])

    def test_one_long_wrong_piece_outweighs_several_right_ones(self):
        v = judge([("in", 11_000, 500), ("in2", 13_000, 500), ("in", 15_000, 500), ("out", 41_000, 3000)])
        self.assertEqual((v["lenient"], v["strict"], v["duration_milli"], v["severe"]), (True, False, 333, True))

    def test_right_pieces_with_a_claim_left_unshown_are_severe(self):
        # every piece is inside the range, but the sentence says a title is displayed and no piece shows it
        v = judge([("in", 11_000, 3000)], text="Dòng chữ lớn The Battle of Russia hiện lên.")
        self.assertEqual((v["lenient"], v["strict"], v["duration_milli"]), (True, True, 1000))
        self.assertEqual(v["severe_reasons"], ["claim_unshown_or_contradicted"])

    def test_pieces_for_different_parts_of_one_sentence(self):
        # a sentence whose footage is two places (two ranges): a piece in each is strictly right
        gt = {"text": "x", "ranges": [[10.0, 20.0], [40.0, 50.0]], "kind": "footage"}
        v = judge([("in", 11_000, 2000), ("out", 41_000, 2000)], gt=gt)
        self.assertEqual((v["strict"], v["duration_milli"], v["severe"]), (True, 1000, False))

    def test_a_missing_unit_ready_is_severe_whatever_it_shows(self):
        v = judge([("in", 11_000, 3000)], gt={"text": "x", "ranges": [], "kind": "missing"})
        self.assertEqual(v["severe_reasons"], ["ready_on_missing"])

    def test_ready_with_no_footage_is_severe(self):
        v = w3.judge({"unit_id": "u", "text": "x", "chosen": [], "pieces": [], "review": {"state": "READY"}}, GT, CANDS)
        self.assertEqual(v["severe_reasons"], ["no_footage"])

    def test_direct_claim_versus_illustrative_broll(self):
        # a sentence about a scene (no displayed claim): B-roll outside the range is judged by time alone; a sentence
        # that says a title is displayed needs a piece that shows it, inside the range or not
        broll = judge([("in", 11_000, 2000), ("out", 41_000, 1000)])
        self.assertEqual((broll["duration_milli"], broll["severe"]), (666, False))
        direct = judge([("title", 10_000, 960), ("in2", 12_000, 2000)], text="Dòng chữ lớn The Battle of Russia hiện lên.")
        self.assertEqual((direct["strict"], direct["claim_failure"], direct["severe"]), (True, False, False))

    def test_the_trimmed_piece_is_judged_not_the_whole_shot(self):
        # the shot overlaps the range, the part of it placed on the timeline does not
        v = judge([("wide", 25_000, 4000)])
        self.assertTrue(w3.hits(CANDS["wide"], GT["ranges"]))
        self.assertEqual((v["lenient"], v["duration_milli"], v["severe"]), (False, 0, True))

    def test_an_older_log_without_pieces_falls_back_to_the_chosen_extents_and_says_so(self):
        unit = {"unit_id": "u", "text": "x", "chosen": ["in"], "review": {"state": "READY"}}
        v = w3.judge(unit, GT, CANDS)
        self.assertTrue(v["extent_fallback"])
        self.assertEqual((v["lenient"], v["duration_milli"]), (True, 1000))


class OneContractEverywhere(unittest.TestCase):
    def test_score_and_attribute_read_the_same_severe_list(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = w3.load_run(make_run(root))
            gt = w3.load_gt(*make_truth(root))
            # make every unit READY in a copy, so the contract decides alone
            for log in run.logs.values():
                for unit in log["units"]:
                    unit["review"] = {**(unit.get("review") or {}), "state": "READY"}
            scored = w3.score(gt, run)
            attributed = w3.attribute(gt, run)
            found = w3.units_by_text(run)
            by_judge = sorted(found[w3.norm(g["text"])][1]["unit_id"] for g in gt["units"]
                              if w3.judge(found[w3.norm(g["text"])][1], g,
                                          w3.candidates_of(run.logs[found[w3.norm(g["text"])][0]]))["severe"])
            self.assertEqual(sorted(scored["severe"]), by_judge)
            self.assertEqual(scored["serious_silent_misses"], scored["severe"])
            self.assertEqual(sorted(r["unit"] for r in attributed["rows"] if r.get("r7") == "SILENT_MISS"
                                    or r.get("stage") == "R7_MISSING_READY"), by_judge)
            self.assertTrue(by_judge)  # the fixture has a READY unit that is wrong
            ready = scored["denominators"]["ready_footage"]
            self.assertEqual(scored["lenient_correct"][1], ready)
            self.assertEqual(scored["strict_correct"][1], ready)
            self.assertEqual(scored["duration_weighted_ready"][1], 1000 * ready)


if __name__ == "__main__":
    unittest.main()

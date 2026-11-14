"""#837: a sentence is READY only when every piece on its timeline shows what it says is displayed.

rc16 W3 ``i1.u2`` ("Dòng chữ lớn The Battle of Russia hiện lên trên nền huy hiệu đại bàng."): 960 ms of the title card,
then 2730 ms of a quotation card on the same eagle crest, READY at 720/736. The title was verified for the sentence by
the first piece; the second piece showed none of it and rode on that verdict, its own confidence coming from the words
it shares ("nền huy hiệu đại bàng"). The vision texts below are the saved rc16 answers for those two shots.

A displayed claim is now checked per piece: a piece that shows none of the sentence's displayed claims is flagged, the
sentence needs a look and says how long that piece is on screen. A sentence that says two things are displayed may show
each in its own piece (a montage); a sentence about a scene (no displayed claim) keeps its per-piece confidence rule.
Synthetic shots only: no media, no network, no AI.
"""
from __future__ import annotations

import random
import unittest

from engine.dubflow.mastering import align, evidence as ev, candidates as shots, claims as said, policy as rules, review as rv

PACING = rules.STYLE_PACING["natural"]
TEXT = "Dòng chữ lớn The Battle of Russia hiện lên trên nền huy hiệu đại bàng."
TITLE = ("Màn hình tiêu đề đen trắng với chữ lớn chồng lên hình huy hiệu đại bàng mờ phía sau. chữ tiêu đề, huy hiệu đại "
         "bàng THE BATTLE OF RUSSIA; WHY WE FIGHT; A SERIES OF SEVEN INFORMATION FILMS; PART 1; Special Service Division; "
         "INFORMATION FILM #5")
QUOTE = ("Màn chữ đen trắng trên nền huy hiệu đại bàng, trích dẫn lời Henry L. Stimson, Bộ trưởng Chiến tranh. huy hiệu "
         "đại bàng, chữ Henry L. Stimson Secretary of War \"We and our allies owe and acknowledge an everlasting debt of "
         "gratitude to the armies and people of the Soviet Union.\"")
MAP = "Bản đồ châu Âu có mũi tên dài, dòng chữ 1500 MILES"


def _candidate(cid: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, "v1", start, end, 0, 50, shots.Quality(120, 50, 300, ()), random.Random(cid).getrandbits(64))


def _seen(text: str, confidence: int = 900, labels: tuple[str, ...] = ("text_on_screen", "establishing")) -> ev.Item:
    return ev.Item("vision", confidence, text, labels, "AI")


def _per_piece(text: str, seen: dict[str, str]) -> dict[str, list[str]]:
    """What each shot's vision answer verifies of the sentence's displayed claims (the planner's own check)."""
    stated = said.of(text)
    return {key: said.check(stated, value).verified for key, value in seen.items()}


def _plan(shots_seen: dict[str, tuple[int, int, tuple[ev.Item, ...]]], text: str = TEXT, length: int = 3690,
          forward_only: bool = True):
    pool = [_candidate(key, a, b) for key, (a, b, _) in shots_seen.items()]
    found = ev.gather(shots_seen)
    for key, (_, _, items) in shots_seen.items():
        for item in items:
            found[key].add(item)
    unit = align.Unit("u1", text, 0, length, "narration")
    choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=forward_only)
    mine = [c for c in choices if c.unit_id == "u1"]
    log = {"mode": mode, "candidates": [{"id": key, "evidence": value.to_dict()} for key, value in found.items()]}
    state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, log, [{"needs_review": c.needs_review} for c in mine])
    return state, {c.candidate.candidate_id: c for c in mine}, decision


class DisplayedClaimPerPiece(unittest.TestCase):
    def test_i1_u2_a_quotation_card_does_not_ride_on_the_title_card(self):
        state, pieces, decision = _plan({"c100011": (58520, 59480, (_seen(TITLE),)), "c100012": (59720, 70680, ()),
                                         "c100017": (81520, 87000, (_seen(QUOTE, 920),))})
        self.assertEqual(set(pieces), {"c100011", "c100017"})  # the same footage as rc16 (the replay's choice)
        self.assertEqual((pieces["c100011"].length_ms, pieces["c100017"].length_ms), (960, 2730))
        self.assertNotEqual(state, rv.READY)  # 3ecaf7d / #836 alone: READY
        self.assertTrue(pieces["c100017"].needs_review)
        self.assertFalse(pieces["c100011"].needs_review)  # the title piece itself is fine
        self.assertTrue(decision.coverage["needs_review"])
        self.assertIn("2.7 giây (73 %) của câu là cảnh không hiển thị điều lời câu nói (The Battle of Russia): cần kiểm tra",
                      decision.notes)
        self.assertEqual(decision.signals["claims"]["verified"], ["The Battle of Russia"])  # sentence level: unchanged
        self.assertEqual(_per_piece(TEXT, {"c100011": TITLE, "c100017": QUOTE}),
                         {"c100011": ["The Battle of Russia"], "c100017": []})

    def test_every_piece_showing_the_title_is_ready(self):
        state, pieces, decision = _plan({"c1": (58520, 59480, (_seen(TITLE),)),
                                         "c2": (59720, 64500, (_seen(TITLE.replace("Màn hình", "Cảnh")),)),
                                         "c3": (81520, 87000, (_seen(QUOTE, 920),))})
        self.assertNotIn("c3", pieces)
        self.assertEqual(state, rv.READY, decision.notes)
        self.assertFalse([n for n in decision.notes if "không hiển thị điều lời câu nói" in n])

    def test_a_montage_may_show_each_displayed_claim_in_its_own_piece(self):
        # two things said to be displayed: the map's 1500 miles, then the title; each piece shows one of them
        text = "Bản đồ ghi khoảng cách một nghìn năm trăm dặm, rồi dòng chữ The Battle of Russia hiện lên."
        state, pieces, decision = _plan({"c1": (0, 2200, (_seen(MAP, 900, ("explanation",)),)),
                                         "c2": (2200, 4600, (_seen(TITLE),)),
                                         "c3": (4600, 12_000, (_seen(QUOTE, 920),))}, text=text, length=4400)
        self.assertEqual(set(pieces), {"c1", "c2"}, decision.to_dict())
        self.assertEqual(_per_piece(text, {"c1": MAP, "c2": TITLE}), {"c1": ["1.500 dặm"], "c2": ["The Battle of Russia"]})
        # the per-piece rule accepts the montage: nothing is flagged for a piece that shows none of the claims ...
        self.assertFalse(decision.coverage["needs_review"])
        self.assertFalse([n for n in decision.notes if "không hiển thị điều lời câu nói" in n])
        # ... READY still needs each piece's own confidence (unchanged): each shows half of a long sentence's words
        for piece in pieces.values():
            self.assertEqual(piece.needs_review, piece.confidence < align.REVIEW_BELOW)

    def test_a_piece_nobody_looked_at_never_rides_on_a_seen_one(self):
        state, pieces, decision = _plan({"c1": (58520, 59480, (_seen(TITLE),)), "c2": (59720, 70680, ())})
        self.assertIn("c2", pieces)
        self.assertNotEqual(state, rv.READY)
        self.assertTrue(pieces["c2"].needs_review)

    def test_the_log_keeps_the_contract_keys(self):
        import json
        from pathlib import Path
        defs = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                          .read_text(encoding="utf-8"))["$defs"]["unit"]["properties"]
        _, _, decision = _plan({"c100011": (58520, 59480, (_seen(TITLE),)), "c100017": (81520, 87000, (_seen(QUOTE, 920),))})
        written = decision.to_dict()
        self.assertLessEqual(set(written["coverage"]), set(defs["coverage"]["properties"]))
        self.assertLessEqual(set(written["signals"]["claims"]), set(defs["signals"]["properties"]["claims"]["properties"]))

    def test_a_sentence_about_a_scene_keeps_its_montage(self):
        # no displayed claim: two seen shots of the same march, each matching on its own, stay READY (no blanket rule)
        text = "Đoàn binh sĩ đội mũ sắt hành quân trên con đường đất dài."
        march = "Đoàn binh sĩ đội mũ sắt hành quân trên con đường đất"
        state, pieces, decision = _plan({"c1": (0, 2000, (_seen(march, 900, ("people", "action")),)),
                                         "c2": (2000, 4000, (_seen(march + " dài", 900, ("people", "action")),)),
                                         "c3": (4000, 9000, (_seen("Bầu trời có mây", 900, ("establishing",)),))},
                                        text=text, length=3900)
        self.assertEqual(set(pieces), {"c1", "c2"})
        self.assertEqual(state, rv.READY, decision.notes)
        self.assertNotIn("claims", decision.signals)

    def test_a_card_contradicting_the_number_is_still_a_conflict(self):
        # #802 unchanged: a map of another amount of the same kind is a conflict, whatever the per-piece rule says
        text = "Bản đồ ghi khoảng cách một nghìn năm trăm dặm."
        state, pieces, decision = _plan({"c1": (0, 4000, (_seen("Bản đồ châu Âu có mũi tên, dòng chữ 500 MILES", 900,
                                                                ("explanation",)),))}, text=text, length=3000)
        self.assertNotEqual(state, rv.READY)
        self.assertTrue(decision.signals["claims"]["conflicts"])


if __name__ == "__main__":
    unittest.main()

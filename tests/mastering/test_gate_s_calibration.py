"""Gate S final closure, the one controlled calibration round on REAL-DEV.

1. Burned-in subtitles are not a text card. Every frame of a subtitled drama was labelled ``text_on_screen`` by the
   vision model, so every shot was held back as a "thẻ chữ" from each sentence not about words: REAL-DEV v3 (the
   tweezers in the nose at 44.8–48.7 s, the sentence's own two best matches) went to unrelated shots and the
   sentence read MATCH_NOT_FOUND. A frame is a card only when the words are all it shows.
2. Each sentence's log carries the separate signals behind its verdict (evidence reliability, semantic relevance,
   temporal coverage, allocation, review risk), which explain a verdict and never change one.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import align
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import policy as rules

PACING = rules.STYLE_PACING["natural"]


def candidate(cid: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, "s1", start, end, 0, 50, shots.Quality(120, 50, 300), random.getrandbits(64))


def seen(cid: str, text: str, labels: tuple[str, ...]) -> ev.Evidence:
    found = ev.Evidence(cid)
    found.add(ev.Item("vision", 850, text, labels, "AI claude"))
    return found


SUBTITLE = "phụ đề tiếng Trung 内容含有恐怖元素请谨慎观看"


def subtitled_drama():
    """A drama with burned-in subtitles on every frame (REAL-DEV v3, reduced)."""
    pool = [candidate("c1", 0, 4000), candidate("c2", 4000, 8000), candidate("c3", 8000, 12_000),
            candidate("c4", 12_000, 16_000), candidate("c5", 16_000, 20_000)]
    found = {
        "c1": seen("c1", f"Đống khăn giấy vo tròn chất cao trên sàn. {SUBTITLE}", ("object", "text_on_screen")),
        "c2": seen("c2", f"Mặt bàn bồn rửa trong nhà tắm, có một chiếc nhíp. {SUBTITLE}", ("object", "text_on_screen")),
        "c3": seen("c3", f"Cận cảnh mũi, tay cầm nhíp đưa vào lỗ mũi để gắp. {SUBTITLE}", ("action", "people", "text_on_screen")),
        "c4": seen("c4", f"Người đàn ông nằm trên giường bệnh, bác sĩ đứng cạnh. {SUBTITLE}", ("people", "text_on_screen")),
        "c5": seen("c5", f"Bầu trời xanh với mây trắng. {SUBTITLE}", ("establishing", "text_on_screen")),
    }
    return pool, found


class SubtitleIsNotACardTests(unittest.TestCase):
    def test_subtitled_footage_is_not_held_back_as_a_text_card(self):
        pool, found = subtitled_drama()
        self.assertFalse(any(align._text_card(found[c.candidate_id]) for c in pool))
        units = [align.Unit("u1", "Khăn giấy chất thành một đống lớn.", 0, 3500, "narration"),
                 align.Unit("u2", "Anh đứng trước bồn rửa, dùng nhíp cố gắp dị vật ra khỏi mũi.", 3500, 7500, "narration"),
                 align.Unit("u3", "Bác sĩ đứng cạnh giường bệnh.", 7500, 11_000, "narration")]
        choices, decisions, mode = align.assign(units, pool, found, PACING, forward_only=True)
        self.assertEqual(mode, "content")
        got = {u: {c.candidate.candidate_id for c in choices if c.unit_id == u} for u in ("u1", "u2", "u3")}
        self.assertEqual(got["u1"], {"c1"})
        self.assertTrue(got["u2"] <= {"c2", "c3"} and got["u2"], got)
        self.assertEqual(got["u3"], {"c4"})
        notes = " ".join(n for d in decisions for n in d.notes)
        self.assertNotIn("thẻ chữ", notes)

    def test_a_screen_of_words_is_still_a_card(self):
        title = seen("t", "Màn hình đen với dòng chữ trắng lớn ở giữa", ("text_on_screen",))
        self.assertTrue(align._text_card(title))
        blurred = seen("b", "Hình mờ, chỉ có chữ", ("reveal", "text_on_screen"))
        self.assertTrue(align._text_card(blurred))
        named = ev.Evidence("n")
        named.add(ev.Item("vision", 850, "Thẻ tiêu đề: NHỮNG CHUYẾN TÀU, chữ bay vào", ("establishing",), "AI"))
        self.assertTrue(align._text_card(named))  # the words of the description still say it is a title card


class SignalsTests(unittest.TestCase):
    def test_each_sentence_logs_its_separate_signals(self):
        pool, found = subtitled_drama()
        units = [align.Unit("u1", "Khăn giấy chất thành một đống lớn.", 0, 3500, "narration"),
                 align.Unit("u2", "Một câu không khớp cảnh nào cả.", 3500, 7000, "narration")]
        _, decisions, _ = align.assign(units, pool, found, PACING, forward_only=True)
        first, second = (d.to_dict()["signals"] for d in decisions)
        self.assertEqual(set(first), {"evidence_reliability", "semantic_relevance", "temporal_coverage", "allocation",
                                      "review_risk"})
        self.assertEqual(first["evidence_reliability"], {"mode": "content", "min_trust": 680, "max_trust": 680})
        self.assertGreater(first["semantic_relevance"]["chosen_min"], 0)
        self.assertEqual(first["temporal_coverage"]["milli"], decisions[0].coverage["milli"])
        self.assertEqual(first["allocation"]["own_ms"], decisions[0].coverage["illustrated_ms"])
        self.assertEqual(second["allocation"]["own_ms"], 0)
        self.assertGreater(second["allocation"]["neutral_ms"], 0)
        self.assertTrue(second["review_risk"]["needs_review"])
        alloc = first["allocation"]
        self.assertEqual(alloc["own_ms"] + alloc["neutral_ms"] + alloc["repeat_ms"] + alloc["held_ms"], units[0].length_ms)

    def test_the_contract_lists_the_signals(self):
        defs = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                          .read_text(encoding="utf-8"))["$defs"]
        pool, found = subtitled_drama()
        _, decisions, _ = align.assign([align.Unit("u1", "Khăn giấy.", 0, 3000, "narration")], pool, found, PACING,
                                       forward_only=True)
        signals = decisions[0].to_dict()["signals"]
        schema = defs["unit"]["properties"]["signals"]
        self.assertEqual(set(schema["required"]), set(signals))
        for name, value in signals.items():
            self.assertEqual(set(schema["properties"][name]["required"]), set(value))


if __name__ == "__main__":
    unittest.main()

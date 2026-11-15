"""#860: an English sentence must not find footage through function words alone.

Track A (USGS, offline): the hard negative "Old Faithful erupted during a winter storm in 1959." became
MATCH_AVAILABLE because the source's speech said "during". Synthetic shots only: no media, no network, no AI.
"""
from __future__ import annotations

import unittest

from engine.dubflow.mastering import align, evidence as ev, review as rv
from tests.mastering.test_transcript_ready_safety import PACING, _candidate, _log

SAID = ev.Item("transcript", 850, "lava flows were active during the last few hundred thousand years", (), "phụ đề nguồn")


def _state(text: str):
    pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
    found = ev.gather(["v1c1", "v1c2"])
    found["v1c1"].add(SAID)
    choices, (decision,), mode = align.assign([align.Unit("u1", text, 0, 4000, "narration")], pool, found, PACING,
                                              forward_only=False)
    return rv.evidence_state({"unit_id": "u1", "coverage": decision.coverage}, _log(mode, found)), decision


class EnglishFunctionWords(unittest.TestCase):
    def test_a_hard_negative_sharing_only_during_finds_no_footage(self):
        state, decision = _state("Old Faithful erupted during a winter storm in 1959.")
        self.assertNotEqual(state, rv.MATCH_AVAILABLE)  # 4af6f61: MATCH_AVAILABLE, 'khớp lời “during”'
        self.assertFalse(decision.coverage.get("matched_footage"))

    def test_a_sentence_sharing_content_words_still_finds_it(self):
        state, _ = _state("Lava flows were active for hundreds of thousands of years.")
        self.assertEqual(state, rv.MATCH_AVAILABLE)

    def test_function_words_give_no_terms_and_content_words_stay(self):
        for word in ("during", "were", "their", "which", "after"):
            self.assertEqual(ev.terms(f"{word}"), frozenset(), word)
        self.assertIn("~lava flows", ev.terms("lava flows during the night"))

    def test_more_function_word_only_negatives_find_no_footage(self):
        # d-s1 offline replay (#860): each of these was MATCH_AVAILABLE on 4af6f61 through function words alone
        for text in ("They were stuck there after the bridge closed.",
                     "Which of these trains would you take when it rains?",
                     "We have been waiting for their pizza since noon.",
                     "Her cat was asleep on the sofa during the film."):
            with self.subTest(text=text):
                self.assertEqual(ev.terms(text) & ev.terms(SAID.text + " since the summer began"), frozenset())

    def test_names_months_and_years_stay_terms(self):
        found = ev.terms("Old Faithful erupted in September 2025 near Norris Geyser Basin")
        for term in ("~old faithful", "~september", "~2025", "~norris geyser", "~erupted"):
            self.assertIn(term, found)

    def test_a_shared_content_word_still_counts(self):
        # lexical retrieval cannot tell a topical near miss from a match: "erupted" is content, not a function word
        state, _ = _state("Old Faithful erupted during a winter storm in 1959 while lava flows were active.")
        self.assertEqual(state, rv.MATCH_AVAILABLE)

    def test_folded_vietnamese_content_words_are_not_dropped(self):
        # "thêm", "thân", "bút", "nốt", "số", "đỏ", "mẹ", "mỹ", "máy", "úp", "hè" fold to English-looking words
        for word in ("them", "than", "but", "not", "so", "do", "me", "my", "may", "up", "he"):
            self.assertIn("~" + word, ev.terms(word), word)

    def test_marked_vietnamese_keeps_its_words(self):
        found = ev.terms("Thêm màu sắc mới cho dòng máy cao cấp")
        for term in ("thêm", "màu", "máy", "cao cấp"):
            self.assertIn(term, found)


if __name__ == "__main__":
    unittest.main()

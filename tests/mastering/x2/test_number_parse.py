"""The research number check reads numbers as specs write them (#873). Synthetic strings only; no source transcript."""
from __future__ import annotations

import unittest

from tests.mastering.x2 import hard_negatives as hn


def number(sentence: str, cue: str) -> str:
    return hn.compare_numbers(sentence, cue)["state"]


class ParseNumber(unittest.TestCase):
    def test_separators_follow_the_text_language(self) -> None:
        self.assertEqual(hn.parse_number("1.800", True), 1800)
        self.assertEqual(hn.parse_number("0,198", True), 0.198)
        self.assertEqual(hn.parse_number("8,4", True), 8.4)
        self.assertEqual(hn.parse_number("631,000", False), 631000)
        self.assertEqual(hn.parse_number("1.800", False), 1.8)
        self.assertEqual(hn.parse_number("1.234.567", True), 1234567)
        self.assertEqual(hn.parse_number("1.234,5", True), 1234.5)

    def test_unreadable_is_none(self) -> None:
        self.assertIsNone(hn.parse_number("1,234.567,8", True))

    def test_units_glued_or_next_word_and_model_ids(self) -> None:
        q = hn.quantities("Máy nặng 210g, sạc 80 W, zoom 120x, chuẩn IP68, giống X9 Pro, Android 16, một tháng")
        dims = {d for d, _, _ in q["measures"]}
        self.assertTrue({"mass", "power", "zoom", "month"} <= dims)
        self.assertEqual({i for _, i in q["models"]}, {"IP68", "X9"})
        self.assertIn(16, q["bare"])


class CompareNumbers(unittest.TestCase):
    def test_changed_value_same_unit_conflicts(self) -> None:
        for sentence, cue in (("nặng 210g", "nặng 198g"), ("sạc 65W", "sạc 80W"), ("zoom 100x", "zoom 120x"),
                              ("tầm 5km", "tầm 3km"), ("108 chấm", "200 chấm"), ("8 tháng trước", "1 tháng trước")):
            with self.subTest(sentence=sentence):
                self.assertEqual(number(sentence, cue), "conflict")

    def test_equivalent_writing_agrees(self) -> None:
        for sentence, cue in (("nặng 0,198 kg", "nặng 198g"), ("sáng 1800 nits", "sáng 1.800 nits"),
                              ("dày 8.4 mm", "dày 8,4 và nhẹ"), ("sạc 80 W", "sạc 80W"), ("một tháng trước", "1 tháng trước"),
                              ("hai màu mới", "2 màu sắc"), ("góc 112 độ", "góc 112°"), ("màn 6,32 inch", "màn 6.32 in")):
            with self.subTest(sentence=sentence):
                self.assertEqual(number(sentence, cue), "agree")

    def test_model_ids_by_letter_prefix(self) -> None:
        self.assertEqual(number("rẻ hơn X9 Pro", "rẻ hơn con X9 Pro"), "agree")
        self.assertEqual(number("rẻ hơn X10 Pro", "rẻ hơn con X9 Pro"), "conflict")
        self.assertEqual(number("chuẩn IP67", "chuẩn IP68 và IP69"), "conflict")
        self.assertEqual(number("giống H8 Pro", "giống H9 Pro"), "conflict")

    def test_nothing_to_check_against_is_unknown_not_conflict(self) -> None:
        self.assertEqual(number("pin 6000 mAh", "pin rất trâu"), "unknown")
        self.assertEqual(number("camera 24 chấm", "camera 32 trậm"), "unknown")
        self.assertEqual(number("giống Z5", "giống con này"), "unknown")

    def test_english_text(self) -> None:
        self.assertEqual(number("about 640,000 years ago", "about 631,000 years ago"), "conflict")
        self.assertEqual(number("about 70,000 years ago", "about 70,000 years ago"), "agree")
        self.assertEqual(number("a magnitude 4.7", "a magnitude 3.7,"), "conflict")

    def test_no_number(self) -> None:
        self.assertEqual(number("The tractor crosses.", "The tractor crosses the river in 2021."), "none")


class Contrast(unittest.TestCase):
    def test_number_and_words_are_reported_apart(self) -> None:
        r = hn.contrast("Sạc đi kèm 65W.", "Sạc vốc đi kèm là 80W")
        self.assertEqual((r["verdict"], r["number"]), ("number_conflict", "conflict"))
        self.assertIn(r["words"], ("substitution", "consistent"))
        self.assertEqual(hn.contrast("pin 6000 mAh", "pin rất trâu")["verdict"], "number_unknown")

    def test_a_number_glued_to_a_unit_is_not_a_content_word(self) -> None:
        self.assertNotIn("80w", hn.content("sạc 80W nhanh")[0])


if __name__ == "__main__":
    unittest.main()

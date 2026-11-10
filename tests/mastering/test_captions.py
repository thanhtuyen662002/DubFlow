"""Captions timed from the final Vietnamese voice (ADR-0055)."""
from __future__ import annotations

import unittest

from engine.dubflow.mastering import captions as c


class CaptionTests(unittest.TestCase):
    def test_a_caption_follows_the_voice_not_the_source_speaker(self):
        cues = [("a", 1000, 2000, "Xin chào"), ("b", 2100, 3000, "Thế giới")]
        # The phrase of cue a was placed later and spoke longer than the source line.
        found = c.from_voice(cues, {"phrase-a": (["a"], 1300, 2900), "phrase-b": (["b"], 3000, 4200)})
        self.assertEqual([(x.cue_id, x.start_ms, x.end_ms) for x in found], [("a", 1300, 2900), ("b", 3000, 4200)])

    def test_members_of_one_phrase_share_it_by_text_length_and_undubbed_cues_keep_their_time(self):
        cues = [("a", 0, 1000, "Một hai"), ("b", 1000, 2000, "Ba bốn năm sáu bảy tám"), ("c", 5000, 7000, "Không lồng tiếng")]
        found = c.from_voice(cues, {"p": (["a", "b"], 200, 3200)})
        a, b, rest = found
        self.assertEqual((a.start_ms, b.end_ms), (200, 3200))
        self.assertTrue(0 <= b.start_ms - a.end_ms <= c.GAP_MS)
        self.assertLess(a.end_ms - a.start_ms, b.end_ms - b.start_ms)
        self.assertEqual((rest.start_ms, rest.end_ms), (5000, 7000))

    def test_long_text_is_wrapped_to_two_lines_and_split_beyond(self):
        text = " ".join(["chữ"] * 40)  # 159 characters
        found = c.from_voice([("a", 0, 8000, text)], {})
        self.assertGreater(len(found), 1)
        for caption in found:
            lines = caption.text.split("\n")
            self.assertLessEqual(len(lines), c.MAX_LINES)
            self.assertTrue(all(len(line) <= c.MAX_CHARS for line in lines), lines)
        self.assertEqual((found[0].start_ms, found[-1].end_ms), (0, 8000))
        self.assertEqual(" ".join(" ".join(x.text.split("\n")) for x in found), text)

    def test_short_captions_stay_a_second_without_overlapping(self):
        found = c.from_voice([("a", 0, 300, "Ừ"), ("b", 600, 900, "Đi"), ("c", 5000, 5200, "Được")], {})
        a, b, last = found
        self.assertEqual(a.end_ms, 600 - c.GAP_MS)  # as long as the next caption allows
        self.assertEqual(b.end_ms, 1600)
        self.assertEqual(last.end_ms, 6000)
        overlapping = c.from_voice([("a", 0, 3000, "Một"), ("b", 1000, 2000, "Hai")], {})
        self.assertLessEqual(overlapping[0].end_ms, overlapping[1].start_ms)


if __name__ == "__main__":
    unittest.main()

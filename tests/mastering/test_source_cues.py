"""#892: a source transcript's bad cues are left out and named, never clamped or invented."""
from __future__ import annotations

import unittest

from engine.dubflow.mastering import evidence as ev

GOOD = "1\n00:00:01,000 --> 00:00:02,500\nXin chào\n"


class CheckSrtTests(unittest.TestCase):
    def reasons(self, block, duration_ms=60_000):
        cues, problems = ev.check_srt(GOOD + "\n" + block, duration_ms)
        self.assertEqual(cues, [(1000, 2500, "Xin chào")], "the good cue is kept unchanged")
        return problems

    def test_a_good_transcript_has_no_problems_and_matches_the_old_parser(self):
        text = "1\n00:00:01,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:04,000\n<i>Bắt đầu</i> thôi\n"
        cues, problems = ev.check_srt(text, 10_000)
        self.assertEqual(problems, [])
        self.assertEqual(cues, ev.parse_srt(text))

    def test_each_kind_of_bad_cue_is_refused_by_number_with_its_reason(self):
        cases = {
            "2\n00:00:30,000 --> 00:00:10,000\nngược\n": "kết thúc trước khi bắt đầu",
            "2\n00:00:05,000 --> 00:00:05,000\nrỗng\n": "dài 0 ms",
            "2\n-00:00:01,000 --> 00:00:02,000\nâm\n": "sai định dạng",
            "2\n00:00:01,5 --> 00:00:02,000\nlẻ\n": "sai định dạng",
            "2\n00:00:01.500 --> \nthiếu\n": "sai định dạng",
            "2\n00:61:00,000 --> 00:62:00,000\nphút\n": "quá 59",
            "2\nchỉ có lời mà không có mốc\n": "thiếu mốc thời gian",
            "2\n00:00:05,000 --> 00:00:06,000\n<i></i>\n": "không có lời",
            "2\n00:01:10,000 --> 00:01:12,000\nquá\n": "bắt đầu sau khi video đã hết",
            "2\n00:00:59,000 --> 00:01:01,000\nvắt qua\n": "kết thúc sau khi video đã hết",
        }
        for block, reason in cases.items():
            with self.subTest(block=block):
                problems = self.reasons(block)
                self.assertEqual(len(problems), 1, problems)
                self.assertEqual(problems[0][0], 2)
                self.assertIn(reason, problems[0][1])

    def test_a_cue_is_never_shortened_to_fit_the_video(self):
        cues, problems = ev.check_srt("1\n00:00:59,000 --> 00:01:01,000\nvắt qua\n", 60_000)
        self.assertEqual(cues, [])
        self.assertEqual(len(problems), 1)
        # without a known length only the timing itself is checked
        self.assertEqual(ev.check_srt("1\n00:00:59,000 --> 00:01:01,000\nvắt qua\n")[0], [(59_000, 61_000, "vắt qua")])

    def test_a_transcript_shifted_past_the_video_is_refused_whole(self):
        # PC E2E F2: every cue moved +40 s on a 64 s source used to drive placement silently
        def at(seconds):
            return f"00:{seconds // 60:02d}:{seconds % 60:02d},000"
        text = "\n\n".join(f"{n}\n{at(40 + n * 5)} --> {at(43 + n * 5)}\ncâu {n}" for n in range(1, 6))
        cues, problems = ev.check_srt(text, 64_000)
        self.assertEqual([c[0] for c in cues], [45_000, 50_000, 55_000, 60_000])
        self.assertEqual([p[0] for p in problems], [5])
        cues, problems = ev.check_srt(text, 20_000)
        self.assertEqual(cues, [])
        self.assertEqual(len(problems), 5)


if __name__ == "__main__":
    unittest.main()

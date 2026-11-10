"""Time at the mastering plan's boundaries: frame rates, long timestamps, adjacency and mapping (Phase 2 §6)."""
from __future__ import annotations

from fractions import Fraction
import unittest

from engine.dubflow.mastering import plan as m

RATES = {"24": Fraction(24), "25": Fraction(25), "30": Fraction(30), "60": Fraction(60),
         "29.97": Fraction(30000, 1001), "23.976": Fraction(24000, 1001), "59.94": Fraction(60000, 1001)}
MS = m.MILLISECONDS
NTSC = m.TimeBase(1, 90000)


def video(source_id: str, duration: int) -> m.Source:
    return m.Source(source_id, duration, 1920, 1080, has_audio=True, rights=m.Rights("owned", "test"))


def contiguous(pieces: list[tuple[str, int, int]], time_base: m.TimeBase = MS) -> m.Plan:
    """A plan from (source, source start, length) pieces laid end to end."""
    at, segments, sources = 0, [], {}
    for index, (source_id, start, length) in enumerate(pieces):
        segments.append(m.Segment(f"s{index}", source_id, m.Span(start, start + length), m.Span(at, at + length)))
        sources[source_id] = max(sources.get(source_id, 0), start + length)
        at += length
    return m.Plan("t", "product_showcase", m.Options("balanced"), at, tuple(video(k, v) for k, v in sources.items()),
                  tuple(segments), m.Provenance("test", "1", "r", ()), time_base=time_base)


class FrameTests(unittest.TestCase):
    def test_frame_boundaries_for_common_and_fractional_rates(self):
        for name, fps in RATES.items():
            with self.subTest(fps=name):
                for frame in (0, 1, 2, 29, 1000, 107_892):  # 107 892 frames ≈ an hour at 29.97
                    exact = Fraction(frame) / fps  # seconds
                    floor_ms = m.frame_floor(int(exact * 1000) + 0, MS, fps)
                    # Inside the frame: snapping down lands on its start (in whole ms, rounded down).
                    self.assertLessEqual(Fraction(floor_ms, 1000), exact + Fraction(1, 1000))
                    self.assertGreater(Fraction(floor_ms, 1000) + Fraction(1, 1000) + 1 / fps, exact)
                    ceil_ms = m.frame_ceil(floor_ms, MS, fps)
                    self.assertGreaterEqual(Fraction(ceil_ms, 1000), Fraction(floor_ms, 1000))
                    self.assertLess(Fraction(ceil_ms, 1000) - Fraction(floor_ms, 1000), 1 / fps + Fraction(1, 1000))

    def test_snapping_is_idempotent_and_monotonic(self):
        for name, fps in RATES.items():
            with self.subTest(fps=name):
                last = -1
                for ms in range(0, 3000, 7):
                    snapped = m.frame_floor(ms, MS, fps)
                    self.assertLessEqual(snapped, ms)
                    self.assertEqual(m.frame_floor(snapped, MS, fps), snapped)
                    self.assertGreaterEqual(snapped, last)
                    last = snapped

    def test_a_90khz_plan_holds_29_97_frames_exactly(self):
        fps = RATES["29.97"]
        for frame in (1, 1001, 10_000_000):
            ticks = frame * 3003  # 90000 * 1001 / 30000
            self.assertEqual(m.frame_floor(ticks, NTSC, fps), ticks)
            self.assertEqual(m.frame_ceil(ticks, NTSC, fps), ticks)

    def test_invalid_rate(self):
        with self.assertRaises(m.PlanError):
            m.frame_floor(10, MS, Fraction(0))


class SecondsTests(unittest.TestCase):
    def test_exact_text_without_floats(self):
        self.assertEqual(m.seconds_text(0, MS), "0.000000")
        self.assertEqual(m.seconds_text(1, MS), "0.001000")
        self.assertEqual(m.seconds_text(3003, NTSC), "0.033367")
        # Ten hours and a day in ms and at 90 kHz: no precision lost to a float.
        self.assertEqual(m.seconds_text(36_000_001, MS), "36000.001000")
        self.assertEqual(m.seconds_text(86_400 * 90000 + 1, NTSC), "86400.000011")
        self.assertEqual(m.seconds_text(2**53 + 1, MS), f"{(2**53 + 1) // 1000}.{(2**53 + 1) % 1000:03d}000")


class MappingTests(unittest.TestCase):
    def test_sources_with_different_rates_map_both_ways(self):
        # A 25 fps clip, a 29.97 clip and a 60 fps clip cut at their own frame starts, end to end.
        pieces = []
        for source_id, fps, start_ms, length_ms in (("a", RATES["25"], 1234, 2000), ("b", RATES["29.97"], 5005, 1500),
                                                    ("c", RATES["60"], 17, 999)):
            start = m.frame_ceil(start_ms, MS, fps)
            end = m.frame_floor(start + length_ms, MS, fps)
            pieces.append((source_id, start, end - start))
        made = contiguous(pieces)
        at = 0
        for segment in made.segments:
            self.assertEqual(segment.timeline.start, at)  # no gap, no overlap
            self.assertGreater(segment.timeline.length, 0)
            at = segment.timeline.end
            for tick in (segment.timeline.start, segment.timeline.end - 1):
                source_id, source_tick = made.to_source(tick)
                self.assertEqual(source_id, segment.source_id)
                self.assertIn(tick, made.to_timeline(source_id, source_tick))
        self.assertEqual(at, made.duration)

    def test_long_timestamps_survive_a_round_trip(self):
        hours = 10 * 3600 * 1000
        made = contiguous([("a", hours, 5000), ("b", 2 * hours, 7000)])
        loaded = m.parse(made.to_json())
        self.assertEqual(loaded.segments[1].source.start, 2 * hours)
        self.assertEqual(loaded.to_source(5000), ("b", 2 * hours))
        as_90k = [m.rescale(s.source.start, MS, NTSC) for s in loaded.segments]
        self.assertEqual(as_90k, [hours * 90, 2 * hours * 90])

    def test_adjacent_segments_and_bad_spans_are_checked(self):
        made = contiguous([("a", 0, 1), ("a", 1, 1), ("a", 2, 1)])  # 1 ms pieces right next to each other
        self.assertEqual([s.timeline.start for s in made.segments], [0, 1, 2])
        with self.assertRaises((m.PlanError, ValueError)):
            m.Span(5, 4)  # negative length
        good = contiguous([("a", 0, 1000), ("a", 1000, 1000)])
        bad = good.to_dict()
        bad["segments"][1]["timeline"]["start"] = "1001"  # a gap
        with self.assertRaises(m.PlanError):
            m.Plan.from_dict(bad)
        bad["segments"][1]["timeline"]["start"] = "999"  # an overlap
        with self.assertRaises(m.PlanError):
            m.Plan.from_dict(bad)

    def test_captions_on_the_narration_timeline_stay_inside_the_cut_plan(self):
        from engine.dubflow.product import script
        spoken = [("Một câu ngắn.", 1300), ("Một câu dài hơn một chút để có hai dòng phụ đề trong khung.", 4100)]
        captions, total = script.captions(spoken)
        made = contiguous([("a", 0, 2000), ("b", 300, total - 2000)])
        self.assertEqual(made.duration, total)
        for caption in captions:
            self.assertLess(caption.start_ms, caption.end_ms)
            made.segment_at(caption.start_ms)  # every caption starts over a picture
            self.assertLessEqual(caption.end_ms, made.duration)


if __name__ == "__main__":
    unittest.main()

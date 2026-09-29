from __future__ import annotations

from decimal import Decimal
import unittest

from engine.dubflow.ocr.orientation import AnimatedTextTracker, OrientationBenchmark, OrientedObservation, Polygon


def observation(identifier: str, frame: int, text: str, polygon: Polygon, angle: int, *, progress: int | None = None, confidence: str = "0.9") -> OrientedObservation:
    return OrientedObservation(identifier, frame, frame * 100, frame * 100 + 100, text, polygon, angle, confidence, progress)


class OrientationTests(unittest.TestCase):
    def test_vertical_diagonal_perspective_polygons_are_retained(self) -> None:
        tracker = AnimatedTextTracker()
        tracks = tracker.track("job-orientation", [
            observation("vertical", 0, "縦字幕", Polygon(((100, 100), (140, 100), (140, 300), (100, 300))), 90_000),
            observation("diagonal", 0, "斜め", Polygon(((200, 200), (360, 240), (350, 280), (190, 240))), 14_000),
            observation("perspective", 0, "台詞", Polygon(((400, 400), (600, 420), (580, 480), (380, 460))), 5_000),
        ])
        self.assertEqual(len(tracks), 3)
        self.assertTrue(all(track.segments[0]["polygon"]["points"] for track in tracks))
        self.assertTrue(all(track.decision == "keep" for track in tracks))

    def test_karaoke_evolution_stays_in_one_track(self) -> None:
        polygon = Polygon(((0, 800), (400, 800), (400, 860), (0, 860)))
        tracks = AnimatedTextTracker().track("job-karaoke", [
            observation("k0", 0, "la la", polygon, 0, progress=100),
            observation("k1", 1, "la la", Polygon(((2, 800), (402, 800), (402, 860), (2, 860))), 0, progress=500),
            observation("k2", 2, "la la", polygon, 0, progress=1000),
        ])
        self.assertEqual(len(tracks), 1)
        self.assertEqual([segment["karaoke_progress_milli"] for segment in tracks[0].segments], [100, 500, 1000])

    def test_low_confidence_falls_back_to_review_safe_rendering(self) -> None:
        polygon = Polygon(((0, 0), (100, 0), (100, 100), (0, 100)))
        tracks = AnimatedTextTracker().track("job-fallback", [observation("bad", 0, "?", polygon, 0, confidence="0.2")])
        self.assertEqual(tracks[0].decision, "review")

    def test_benchmark_is_machine_readable(self) -> None:
        benchmark = OrientationBenchmark("orientation-v1", Decimal("0.95"), Decimal("0.05"), Decimal("0.98"), Decimal("0.04"), 30, 4096, "PROMOTE")
        payload = benchmark.to_dict()
        self.assertEqual(payload["decision"], "PROMOTE")
        self.assertEqual(payload["oriented_recall"], "0.95")

    def test_invalid_polygon_or_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Polygon(((0, 0), (1, 1), (2, 2), (3, 3)))
        with self.assertRaises(ValueError):
            observation("bad", 0, "x", Polygon(((0, 0), (10, 0), (10, 10), (0, 10))), 180_001)


if __name__ == "__main__":
    unittest.main()

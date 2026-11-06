"""A 7-20 hour source: FFmpeg's hang guard grows with it and the render reports a percentage."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from engine.dubflow.worker import production_job as worker


class Emitter:
    def __init__(self):
        self.events = []

    def progress(self, fraction, detail, **_):
        self.events.append((round(fraction, 4), detail))


class LongRenderTests(unittest.TestCase):
    def test_timeout_outgrows_a_twenty_hour_render(self):
        twenty_hours = SimpleNamespace(duration_ticks=20 * 3_600_000)
        self.assertGreaterEqual(worker._media_timeout(twenty_hours), 6 * 20 * 3600)
        self.assertEqual(worker._media_timeout(SimpleNamespace(duration_ticks=None)), 15 * 60)

    def test_render_progress_is_a_bounded_throttled_percentage(self):
        emitter = Emitter()
        report = worker._render_reporter(emitter, "Đang render", SimpleNamespace(duration_ticks=10_000))
        report(5_000_000)
        report(6_000_000)  # Within two seconds: dropped.
        self.assertEqual(emitter.events, [(0.87, "Đang render 50%")])

    def test_progress_label_names_the_gpu_only_for_nvenc(self):
        self.assertEqual(worker._render_device(SimpleNamespace(video_encoders=lambda: ("h264_nvenc", "h264_mf"))), " bằng GPU NVIDIA")
        self.assertEqual(worker._render_device(SimpleNamespace(video_encoders=lambda: ("h264_mf",))), "")
        self.assertEqual(worker._render_device(object()), "")


if __name__ == "__main__":
    unittest.main()

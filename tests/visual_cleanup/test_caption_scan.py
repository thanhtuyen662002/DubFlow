from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from engine.dubflow.ocr import caption_scan as scan
from engine.dubflow.ocr.caption_scan import TextBox, line_boxes, plan_regions


def caption(text_seed: int, *, y: float = 0.85, x: float = 0.3, w: float = 0.4, h: float = 0.05) -> TextBox:
    # A different signature per caption text, like changing dialogue.
    return TextBox(x, y, w, h, tuple(float((text_seed * 37 + i * 11) % 255) for i in range(64)))


LOGO = TextBox(0.82, 0.86, 0.14, 0.04, tuple(100.0 for _ in range(64)))


class LineBoxTests(unittest.TestCase):
    def test_rows_and_close_columns_join_into_unclipped_lines(self):
        prob = np.zeros((100, 200), np.float32)
        prob[40:46, 20:60] = 0.9
        prob[40:46, 64:120] = 0.9  # gap of 4 px < line height: same line
        prob[80:84, 150:190] = 0.9  # separate line
        boxes = line_boxes(prob)
        self.assertEqual(len(boxes), 2)
        left, top, right, bottom = boxes[0]
        # Grown beyond the kernel on every side, but kept inside the map.
        self.assertTrue(left < 20 and top < 40 and right > 120 and bottom > 46)
        self.assertTrue(0 <= left and 0 <= top and right <= 200 and bottom <= 100)
        self.assertEqual(line_boxes(np.zeros((32, 32), np.float32)), [])

    def test_ppm_header_reader_handles_frames_and_end_of_stream(self):
        stream = io.BytesIO(b"P6\n4 2\n255\n" + bytes(24) + b"P6 4 2 255\n")
        self.assertEqual(scan._read_ppm_header(stream), (4, 2))
        stream.read(24)
        self.assertEqual(scan._read_ppm_header(stream), (4, 2))
        self.assertIsNone(scan._read_ppm_header(io.BytesIO(b"")))
        with self.assertRaises(ValueError):
            scan._read_ppm_header(io.BytesIO(b"P5\n4 2\n255\n"))


class PlanRegionTests(unittest.TestCase):
    def samples(self, seconds: float, boxes_at):
        return [(i / scan.SAMPLE_FPS, boxes_at(i / scan.SAMPLE_FPS)) for i in range(int(seconds * scan.SAMPLE_FPS))]

    def test_changing_captions_blur_while_on_screen_and_a_static_logo_is_kept(self):
        def boxes_at(t):
            boxes = [LOGO]
            if 1 <= t < 4:
                boxes.append(caption(1))
            elif 6 <= t < 9:
                boxes.append(caption(2, w=0.5, x=0.25))
            return boxes

        result = plan_regions(self.samples(10, boxes_at), 10.0)
        self.assertEqual([(r.start_ms, r.end_ms) for r in result.regions], [(750, 3750), (5750, 8750)])
        # Padded around the text and never covering the logo on the right.
        for region in result.regions:
            self.assertLess(region.y, 0.85)
            self.assertLess(region.x + region.w, LOGO.x)
        self.assertEqual(len(result.fixed_text), 1)
        self.assertEqual(result.summary()["kept_fixed_text"][0]["x"], 0.82)

    def test_a_never_changing_centered_caption_is_still_blurred(self):
        static = TextBox(0.2, 0.9, 0.6, 0.04, tuple(80.0 for _ in range(64)))
        result = plan_regions(self.samples(6, lambda t: [static]), 6.0)
        self.assertEqual(len(result.regions), 1)
        self.assertEqual((result.regions[0].start_ms, result.regions[0].end_ms), (0, 5750))

    def test_text_away_from_the_caption_area_is_ignored(self):
        high = caption(3, y=0.3)
        side = caption(4, x=0.02, w=0.2)
        result = plan_regions(self.samples(4, lambda t: [high, side]), 4.0)
        self.assertEqual(result.regions, ())

    def test_speech_gate_drops_text_shown_without_dialogue(self):
        def boxes_at(t):
            return [caption(int(t))] if t < 3 or 5 <= t < 8 else []

        speech = [(5000, 8000)]
        result = plan_regions(self.samples(9, boxes_at), 9.0, speech_ms=speech)
        self.assertEqual([(r.start_ms, r.end_ms) for r in result.regions], [(4750, 7750)])
        self.assertEqual(len(plan_regions(self.samples(9, boxes_at), 9.0).regions), 2)

    def test_two_caption_lines_are_tracked_separately_and_regions_are_capped(self):
        def two_lines(t):
            return [caption(int(t), y=0.80), caption(int(t) + 50, y=0.88)]

        result = plan_regions(self.samples(4, two_lines), 4.0)
        self.assertEqual(len(result.regions), 2)
        # Captions flashing on and off every second would need hundreds of regions.
        flicker = plan_regions([(i * 2.0, [caption(i)]) for i in range(400)], 800.0)
        self.assertLessEqual(len(flicker.regions), scan.MAX_BLUR_REGIONS)


MODEL = os.environ.get("DUBFLOW_TEST_OCR_MODEL")
FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG")


@unittest.skipUnless(MODEL and FFMPEG, "set DUBFLOW_TEST_OCR_MODEL and DUBFLOW_TEST_FFMPEG to run against the real detector")
class RealScannerTests(unittest.TestCase):
    def test_burned_caption_is_found_and_a_corner_logo_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            font = Path(r"C:\Windows\Fonts\consola.ttf")
            if not font.is_file():
                self.skipTest("needs a TrueType font")
            (root / "font.ttf").write_bytes(font.read_bytes())
            text = "drawtext=fontfile=font.ttf:fontsize=40:fontcolor=white:borderw=3"
            graph = (f"{text}:text='HELLO WORLD CAPTION':x=(w-tw)/2:y=h*0.85:enable='between(t,1,3)',"
                     f"{text}:text='ANOTHER LINE HERE':x=(w-tw)/2:y=h*0.85:enable='between(t,4,6)',"
                     f"{text}:text='LOGO':x=w-tw-20:y=h*0.9")
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=0x203040:s=960x540:d=7:r=10", "-vf", graph,
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "video.mp4"], check=True, cwd=root)
            result = scan.CaptionScanner(Path(MODEL), Path(FFMPEG)).scan(root / "video.mp4", duration_s=7.0)
            self.assertGreaterEqual(len(result.regions), 2, result.summary())
            first = result.regions[0]
            self.assertLessEqual(first.start_ms, 1250)
            self.assertGreater(first.y + first.h, 0.86)
            self.assertTrue(all(region.x + region.w < 0.85 for region in result.regions))
            self.assertEqual(len(result.fixed_text), 1)


if __name__ == "__main__":
    unittest.main()

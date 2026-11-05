from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    import numpy as np
except ImportError:  # the CI lane has no numpy; the app runtime does.
    np = None

from engine.dubflow.ocr import caption_scan as scan
from engine.dubflow.ocr.caption_scan import TextBox, line_boxes, plan_regions


def caption(text_seed: int, *, y: float = 0.85, x: float = 0.3, w: float = 0.4, h: float = 0.05) -> TextBox:
    # A different signature per caption text, like changing dialogue.
    return TextBox(x, y, w, h, tuple(float((text_seed * 37 + i * 11) % 255) for i in range(64)))


LOGO = TextBox(0.82, 0.86, 0.14, 0.04, tuple(100.0 for _ in range(64)))


class LineBoxTests(unittest.TestCase):
    @unittest.skipUnless(np, "needs numpy")
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
        # Caption rows (where captions sit, for documentary framing) are also only those seen during speech.
        self.assertTrue(result.caption_rows)
        self.assertTrue(all(4.5 <= time < 8.5 and (top, bottom) == (0.85, 0.9) for time, top, bottom in result.caption_rows))
        self.assertEqual(result.summary()["caption_rows"], len(result.caption_rows))
        self.assertEqual(len(plan_regions(self.samples(9, boxes_at), 9.0).regions), 2)

    def test_two_caption_lines_are_tracked_separately_and_regions_are_capped(self):
        def two_lines(t):
            return [caption(int(t), y=0.80), caption(int(t) + 50, y=0.88)]

        result = plan_regions(self.samples(4, two_lines), 4.0)
        self.assertEqual(len(result.regions), 2)
        # Captions flashing on and off every second would need hundreds of regions.
        flicker = plan_regions([(i * 2.0, [caption(i)]) for i in range(400)], 800.0)
        self.assertLessEqual(len(flicker.regions), scan.MAX_BLUR_REGIONS)


class LogoTests(unittest.TestCase):
    def test_a_see_through_logo_over_moving_footage_is_a_logo(self):
        # 2026-11-04 sources: "太空记" sat in one place in 126/144 samples, but its pixels changed with the footage.
        def boxes(i):
            logo = TextBox(0.055 + (i % 3) * 0.002, 0.694, 0.128, 0.094, tuple(float((i * 53 + k * 7) % 255) for k in range(64)))
            line = caption(i, y=0.88, x=0.27, w=0.46)
            title = TextBox(0.35, 0.45, 0.3, 0.1, ()) if i % 10 == 0 else None
            return [logo, line] + ([title] if title else [])
        found = scan.logo_boxes([boxes(i) for i in range(40)])
        self.assertEqual(len(found), 1, found)
        self.assertAlmostEqual(found[0].x, 0.057, places=2)
        # A caption that never moves is not a logo, nor is a sign filling half the frame.
        self.assertEqual(scan.logo_boxes([[caption(i, y=0.88)] for i in range(10)]), [])
        self.assertEqual(scan.logo_boxes([[TextBox(0.1, 0.1, 0.6, 0.3, ())] for _ in range(10)]), [])

    def test_a_channel_name_beside_a_logo_joins_it(self):
        # src_03 of the 2026-11-05 tier-2 film: a grey "剑指桃花开 bilibili" watermark with the gold channel name
        # "科普足迹" under it, which slides in from the left in the first 3 s and then flickers in height.
        def boxes(i):
            found = [TextBox(0.016, 0.025 + (i % 2) * 0.003, 0.192, 0.075 + (i % 3) * 0.004, ())]
            if i % 4 != 3:
                found.append(TextBox(0.067, 0.078, 0.181, 0.075 + (i % 5) * 0.008, ()))
            if i == 7:
                found.append(TextBox(0.3, 0.05, 0.1, 0.05, ()))  # one-off scene text nearby does not stretch it
            return found + [caption(i, y=0.83, x=0.19, w=0.62)]
        samples = [boxes(i) for i in range(40)]
        [logo] = scan.logo_boxes(samples)
        self.assertLessEqual(logo.x, 0.067)
        self.assertGreaterEqual(logo.x + logo.w, 0.247)
        self.assertGreaterEqual(logo.y + logo.h, 0.15)
        self.assertLess(logo.x + logo.w, 0.3)
        # The animation (only "足迹" at the left edge) and the tall flicker are part of the logo, not big text.
        timed = [(0.5, [TextBox(0.0, 0.09, 0.07, 0.05)]), (5.0, [TextBox(0.064, 0.072, 0.188, 0.092)]),
                 (7.5, [TextBox(0.013, 0.414, 0.93, 0.153)])]
        self.assertEqual(scan.big_text_times(timed, [logo]), [7.5])
        # A logo alone stays as it was.
        alone = [[TextBox(0.055, 0.694, 0.128, 0.094, ())] for _ in range(20)]
        self.assertEqual(scan.logo_boxes(alone)[0].w, 0.128)

    def test_big_text_moments_are_found_but_not_logos_or_captions(self):
        logo = TextBox(0.055, 0.694, 0.128, 0.094)
        title = TextBox(0.0, 0.3, 0.98, 0.12)  # "120万头猪要怎么养?" across the frame
        samples = [(0.0, [logo, caption(0)]), (2.0, [logo, title]), (4.0, [logo]), (6.0, [TextBox(0.3, 0.45, 0.3, 0.1)]), (8.0, [TextBox(0.02, 0.04, 0.17, 0.075)])]
        self.assertEqual(scan.big_text_times(samples, [logo]), [2.0, 6.0])
        # A news ticker: two lines across the frame (89-93 % wide in BV1Vy4y1Z7C1); one long caption line is not.
        ticker = [(0.0, [TextBox(0.07, 0.74, 0.93, 0.07), TextBox(0.07, 0.81, 0.9, 0.07)]), (2.0, [TextBox(0.05, 0.84, 0.9, 0.07)])]
        self.assertEqual(scan.big_text_times(ticker), [0.0])
        # Pop-up banners above the caption band: "点赞支持 欢迎关注" (y 0.62) and "搜索 太空记" (y 0.69).
        popups = [(0.0, [TextBox(0.10, 0.62, 0.14, 0.083), TextBox(0.41, 0.62, 0.08, 0.078)]), (2.0, [TextBox(0.27, 0.69, 0.16, 0.117)]),
                  (4.0, [TextBox(0.3, 0.84, 0.4, 0.07)]), (6.0, [TextBox(0.07, 0.08, 0.17, 0.072)])]
        self.assertEqual(scan.big_text_times(popups, band_top=0.86), [0.0, 2.0], "captions and corner tags stay")
        self.assertEqual(scan.big_text_times(popups), [], "without a caption band these are not big text")


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

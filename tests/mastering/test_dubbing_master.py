"""Dubbing mastering decisions and measurements (ADR-0055)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import measure
from engine.dubflow.mastering.measure import GREY_H, GREY_W, Loudness, Picture
from engine.dubflow.mastering.plan import Options
from engine.dubflow.mastering.planners import dubbing

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
QUIET = Loudness(-24.0, -12.0, 7.0, -34.0, 0.4)  # room to rise 8 LU linearly
GOOD = Picture(16, 16, 235, 120, (0, 0, 0, 0))
WASHED = Picture(16, 40, 200, 110, (0, 0, 0, 0))


class DecisionTests(unittest.TestCase):
    def plan(self, level, mix=QUIET, frames=GOOD, **kw):
        return dubbing.plan(Options(level, **kw), mix=mix, mix_rate=48000, frames=frames, width=1920, height=1080)

    def test_off_does_nothing(self):
        master = self.plan("off", frames=WASHED)
        self.assertFalse(master.changes_render)
        self.assertEqual((master.audio_filter, master.video_filters, master.climax), (None, (), None))

    def test_loudness_is_two_pass_linear_and_skipped_when_close_or_silent(self):
        master = self.plan("light")
        self.assertIn("loudnorm=I=-16:TP=-1.5", master.audio_filter)
        self.assertIn("measured_I=-24.00", master.audio_filter)
        self.assertIsNotNone(self.plan("light", mix=Loudness(-24.0, -6.0, 7.0, -34.0, 0.4)).audio_filter)
        self.assertIn("linear=true", master.audio_filter)
        self.assertTrue(master.audio_filter.endswith("aresample=48000"))
        self.assertIsNone(self.plan("light", mix=Loudness(-16.4, -3.0, 6, -26, 0.1)).audio_filter)
        self.assertIsNotNone(self.plan("light", mix=Loudness(-15.8, -0.5, 6, -26, 0.1)).audio_filter)  # peak too high
        self.assertIsNone(self.plan("light", mix=None).audio_filter)
        self.assertIsNone(self.plan("light", mix=Loudness(-60, -40, 1, -70, 0)).audio_filter)

    def test_a_loud_peak_lowers_the_target_so_the_gain_stays_linear(self):
        # real run 9: -15.2 LUFS with a peak near 0 dBTP went dynamic in loudnorm and the AAC peaked at -0.7 dBTP
        master = self.plan("light", mix=Loudness(-15.2, -0.3, 7.6, -25, 0.1))
        target = float(master.audio_filter.split("loudnorm=I=")[1].split(":")[0])
        self.assertLessEqual(-0.3 + (target - -15.2), dubbing.PEAK_DBTP - dubbing.AAC_HEADROOM_DB + 1e-9)
        self.assertIn("linear=true", master.audio_filter)
        self.assertTrue(any("thấp hơn mục tiêu" in note for note in master.notes))
        # a mix at the target whose peak leaves no AAC headroom is turned down, not left as it is
        self.assertIsNotNone(self.plan("light", mix=Loudness(-16.2, -1.8, 6, -26, 0.1)).audio_filter)
        # never asks for less loudness range than the mix has (that would compress)
        wide = self.plan("light", mix=Loudness(-24, -10, 14.2, -34, 0.1)).audio_filter
        self.assertIn(":LRA=15:", wide)

    def test_the_recipe_and_the_post_aac_trim_are_cumulative_bounded_and_never_boost(self):
        """Issue #821: the trim after an AAC trial is counted from the PCM, cumulatively, and only ever lowers."""
        self.assertEqual(dubbing.RECIPE, "dubbing-master-v4")  # v3 plans (no AAC trial) are planned again
        self.assertEqual((dubbing.AAC_TRIM_MARGIN_DB, dubbing.AAC_TRIM_TRIES), (0.3, 3))
        self.assertEqual(dubbing.aac_trim(-1.31), 0.49)  # rc17: (−1.31 + 1.5) + 0.3
        self.assertEqual(dubbing.aac_trim(-1.40, 0.49), 0.89)  # cumulative: measured again after the first trim
        self.assertEqual(dubbing.aac_trim(-1.5), 0.0)  # at the ceiling is within it
        self.assertEqual(dubbing.aac_trim(-2.3, 0.49), 0.49)  # under it: nothing more, never a boost back
        self.assertEqual(dubbing.aac_trim(None, 0.49), 0.49)  # silence: nothing to trim
        trim = 0.0
        for peak in (-1.0, -1.2, -1.1, -1.4):  # never smaller than the last one, whatever was measured
            nxt = dubbing.aac_trim(peak, trim)
            self.assertGreater(nxt, trim)
            trim = nxt
        for invalid in (-0.1, float("nan")):
            with self.assertRaises(ValueError):
                dubbing.aac_trim(-1.0, invalid)
            with self.assertRaises(ValueError):
                dubbing.with_trim(None, invalid)
        self.assertEqual(dubbing.with_trim(None, 0.6), "volume=-0.60dB")
        self.assertEqual(dubbing.with_trim("loudnorm=I=-17.2:linear=true,aresample=48000", 0.49),
                         "loudnorm=I=-17.2:linear=true,aresample=48000,volume=-0.49dB")
        self.assertEqual(dubbing.with_trim("loudnorm=x", 0.0), "loudnorm=x")
        self.assertIsNone(dubbing.with_trim(None, 0.0))

    def test_the_aac_note_has_its_own_prefix(self):
        note = dubbing.aac_note({"first_true_peak": -1.31, "true_peak": -2.29, "trim_db": 0.49, "tries": 2})
        self.assertEqual(note, "Âm lượng sau AAC: giảm thêm 0,5 dB để đỉnh ≤ −1,5 dBTP.")
        self.assertFalse(note.startswith("Âm lượng:"))  # the planner's note (card and B1 fallback look it up)
        failed = dubbing.aac_note({"first_true_peak": -1.31, "true_peak": -1.40, "trim_db": 0.49, "tries": 4})
        self.assertIn("chưa đạt trần −1,5 dBTP", failed)
        self.assertIsNone(dubbing.aac_note({"first_true_peak": -2.6, "true_peak": -2.6, "trim_db": 0.0, "tries": 1}))
        self.assertIsNone(self.plan("light").aac_trial)
        self.assertIn("aac_trial", self.plan("light").to_dict())

    def test_colour_is_a_bounded_level_stretch(self):
        master = self.plan("light", frames=WASHED)
        (colour,) = master.video_filters
        values = dict(part.split("=") for part in colour.removeprefix("colorlevels=").split(":"))
        self.assertLessEqual(float(values["rimin"]), dubbing.COLOR_LIMIT["light"])
        self.assertGreaterEqual(float(values["rimax"]), 1 - dubbing.COLOR_LIMIT["light"])
        self.assertEqual(self.plan("light").video_filters, ())
        self.assertTrue(any("không có viền đen" in n for n in self.plan("balanced").notes))
        flat = Picture(16, 100, 120, 110, (0, 0, 0, 0))  # a fog or night scene on purpose
        self.assertEqual(self.plan("dynamic", frames=flat).video_filters, ())
        wider = dict(p.split("=") for p in self.plan("dynamic", frames=WASHED).video_filters[0][12:].split(":"))
        self.assertGreater(float(wider["rimin"]), float(values["rimin"]))

    def test_only_a_window_box_is_cropped_and_only_from_balanced(self):
        boxed = Picture(16, 16, 235, 120, (6, 6, 8, 8))
        self.assertEqual(self.plan("light", frames=boxed).video_filters, ())
        (crop,) = self.plan("balanced", frames=boxed).video_filters
        self.assertTrue(crop.startswith("crop="))
        width, height = (int(v) for v in crop[5:].split(":")[:2])
        self.assertAlmostEqual(width / height, 1920 / 1080, places=2)  # same shape: no stretch
        self.assertLessEqual(1920 / width, 1.08 + 1e-6)  # within the policy's zoom
        for bars in ((10, 10, 0, 0), (0, 0, 12, 12), (6, 0, 8, 8)):
            self.assertEqual(self.plan("balanced", frames=Picture(16, 16, 235, 120, bars)).video_filters, (), bars)

    def test_captions_follow_the_voice_from_balanced_when_there_is_a_dub(self):
        self.assertEqual(self.plan("light").captions, "plain")
        self.assertEqual(self.plan("balanced").captions, "phrase")
        self.assertEqual(self.plan("dynamic").captions, "phrase")
        no_dub = dubbing.plan(Options("balanced"), mix=None, mix_rate=None, frames=GOOD, width=1920, height=1080)
        self.assertEqual(no_dub.captions, "plain")

    def test_the_climax_is_a_suggestion_only_when_asked(self):
        found = {"start_ms": 61000, "end_ms": 65000, "reasons": ["âm thanh lớn nhất"]}
        self.assertIsNone(dubbing.plan(Options("dynamic"), mix=QUIET, mix_rate=48000, frames=GOOD, width=1920, height=1080,
                                       climax=found).climax)
        asked = dubbing.plan(Options("dynamic", enable_climax_hook=True), mix=QUIET, mix_rate=48000, frames=GOOD, width=1920,
                             height=1080, climax=found)
        self.assertEqual((asked.climax["needs_review"], asked.climax["applied"]), (True, False))
        json.dumps(asked.to_dict(), ensure_ascii=False)


class MeasureTests(unittest.TestCase):
    def test_the_loudest_window_is_found_inside_the_video(self):
        import wave
        from array import array
        with TemporaryDirectory() as folder:
            path = Path(folder) / "a.wav"
            rate = 8000
            samples = array("h", [0] * (rate * 60))
            for i in range(rate * 40, rate * 45):  # a burst at 40–45 s
                samples[i] = 12000 if i % 2 else -12000
            for i in range(rate * 1, rate * 4):    # louder, but in the first 20 %: not a climax
                samples[i] = 30000 if i % 2 else -30000
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(samples.tobytes())
            found = measure.loudest_window(path)
            self.assertEqual((found["start_ms"], found["end_ms"]), (40000, 45000))
            self.assertIsNone(measure.loudest_window(Path(folder) / "missing.wav"))


    def test_frame_times_and_bars(self):
        self.assertEqual(measure.frame_times(10000, 2), [2650, 7350])
        self.assertEqual(measure.frame_times(0, 4), [])
        frame = bytearray([128] * (GREY_W * GREY_H))
        for y in range(5):
            frame[y * GREY_W:(y + 1) * GREY_W] = bytes(GREY_W)
            frame[(GREY_H - 1 - y) * GREY_W:(GREY_H - y) * GREY_W] = bytes(GREY_W)
        found = measure.picture([bytes(frame)])
        self.assertEqual(found.bars, (5, 5, 0, 0))
        self.assertIsNone(measure.picture([]))

    def test_loudness_parses_and_silence_is_none(self):
        answer = b'[Parsed_loudnorm_0 @ 0x1]\n{\n"input_i" : "-23.10",\n"input_tp" : "-4.20",\n"input_lra" : "5.00",\n' \
                 b'"input_thresh" : "-33.40",\n"output_i" : "-16.0",\n"target_offset" : "0.30"\n}\n'
        found = measure.loudness(Path("ffmpeg"), Path("a.wav"), capture=lambda argv: (b"", answer))
        self.assertEqual(found, Loudness(-23.1, -4.2, 5.0, -33.4, 0.3))
        silent = answer.replace(b'"-23.10"', b'"-inf"')
        self.assertIsNone(measure.loudness(Path("ffmpeg"), Path("a.wav"), capture=lambda argv: (b"", silent)))


@unittest.skipUnless(FFMPEG, "needs FFmpeg")
class RealMediaTests(unittest.TestCase):
    def test_a_quiet_mix_is_brought_to_the_target_and_the_picture_is_measured(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            mix, video = root / "mix.wav", root / "v.mp4"
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-af", "volume=0.2",
                            "-ar", "48000", "-c:a", "pcm_s16le", str(mix)], check=True)
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=4", "-vf",
                            "pad=400:240:40:30:black", "-c:v", "mpeg4", str(video)], check=True)
            found = measure.loudness(Path(FFMPEG), mix)
            self.assertLess(found.integrated, -25)
            master = dubbing.plan(Options("balanced"), mix=found, mix_rate=48000,
                                  frames=measure.picture(measure.grey_frames(Path(FFMPEG), video, measure.frame_times(4000, 4))),
                                  width=400, height=240)
            out = root / "mastered.wav"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(mix), "-af", master.audio_filter, "-c:a", "pcm_s16le", str(out)],
                           check=True)
            after = measure.loudness(Path(FFMPEG), out)
            self.assertLess(abs(after.integrated - dubbing.TARGET_LUFS), 1.0)
            self.assertLessEqual(after.true_peak, dubbing.PEAK_DBTP + 0.3)
            self.assertTrue(any(f.startswith("crop=") for f in master.video_filters), master.notes)  # padded on all sides
            graded = root / "graded.mp4"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(video), "-vf", ",".join(master.video_filters), "-frames:v", "5",
                            "-c:v", "mpeg4", str(graded)], check=True)


if __name__ == "__main__":
    unittest.main()

"""Gate C, A/V sync at every boundary (P2-9), measured from the decoded output, never from container start times.

Synthetic media first (the directive's order): each piece of a test part has its own colour and its own tone, so
the frame where the colour changes and the 2 ms window where the tone changes are the boundary's decoded video
and audio times. Sources at CFR 24, 25 and 29.97, VFR, mixed rates in one part, sources without sound, stills
and pieces shorter than a frame; then a join of ten parts, measured with the marker method at every piece edge
and with the reference method (``sync_probe.measure_joins``, the one used on real footage) at every join.

Thresholds are the directive's and are not changed here: per boundary ≤ max(40 ms, one frame), cumulative
≤ 80 ms. Pictures are also held to the plan: within half a frame (+2 ms, the tone window) of every planned cut.
The old join (stream copy of picture and sound) is kept as the failing baseline.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import json
import wave

from engine.dubflow.compilation import render
from engine.dubflow.mastering import sync_probe as probe
from engine.dubflow.package.render import Video
from engine.dubflow.product import render as product_render

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")

COLOURS = [(255, 0, 0), (0, 128, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255)]
NAMES = ["red", "green", "blue", "yellow", "magenta", "cyan"]
TONES = [700, 1700, 2900, 4300, 5900, 7700]        # a source's sound (well apart for 2 ms windows)
VOICE = [1100, 2300, 3600, 5100, 6800, 8700]       # the narration's, one per piece
FRAME_MS = 1000 / render.FPS
REPORT = os.environ.get("DUBFLOW_AV_REPORT")  # a file to append every measurement to (the Gate C report's numbers)


def keep(label, found):
    if REPORT:
        with open(REPORT, "a", encoding="utf-8") as out:
            out.write(json.dumps({"case": label, **found}, ensure_ascii=False) + "\n")
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def edges(lengths):
    out, at = [], 0
    for length in lengths[:-1]:
        at += length
        out.append(at)
    return out


class FrameGridTests(unittest.TestCase):
    """The cut positions alone (no FFmpeg): every cut on one grid, never drifting from the plan."""

    def check(self, counts, lengths, fps):
        self.assertTrue(all(c >= 1 for c in counts))
        at = done = 0
        for count, length in zip(counts, lengths):
            at += length
            done += count
            if length * fps >= 1000:  # a piece of a frame or more: its cut within half a frame of the plan
                self.assertLessEqual(abs(done * 1000 / fps - at), 500 / fps + 1e-9)
        self.assertEqual(done, (sum(lengths) * fps + 500) // 1000)

    def test_cuts_stay_on_the_grid_however_many_pieces(self):
        lengths = [1237, 2011, 1550, 997, 3123, 1801, 1333, 2222, 1717, 1919] * 20
        self.check(render.frame_counts(lengths), lengths, render.FPS)
        self.check(product_render.frame_counts(lengths), lengths, product_render.FPS)

    def test_the_old_per_piece_rounding_drifts(self):  # the reproduction, kept as the baseline
        lengths = [1237, 2011, 1550, 997, 3123, 1801, 1333, 2222, 1717, 1919]
        per_piece = [math.ceil(length * render.FPS / 1000) for length in lengths]  # trim=duration rounds each up
        drift = sum(per_piece) * FRAME_MS - sum(lengths)
        self.assertGreater(drift, FRAME_MS)
        self.assertEqual(sum(render.frame_counts(lengths)), round(sum(lengths) * render.FPS / 1000))

    def test_a_piece_shorter_than_a_frame_is_one_frame_and_the_next_cut_catches_up(self):
        counts = render.frame_counts([20, 20, 20, 1000])
        self.assertEqual(counts[:3], [1, 1, 1])
        self.assertEqual(sum(counts), 32)  # 1060 ms is frame 32: the long piece gives the extra frames back

    def test_a_part_is_whole_frames_and_its_sound_exactly_as_long(self):
        for ms in (1000, 1017, 33_333, 61_234):
            samples = render.part_samples(ms)
            self.assertEqual(samples % render.FRAME_SAMPLES, 0)
            self.assertLessEqual(abs(samples / 48 - ms), FRAME_MS / 2 + 1e-9)


class LegacyArgvTests(unittest.TestCase):
    """Compilation v1 (clips, then the package join) keeps its commands byte for byte."""

    def test_the_v1_joins_are_unchanged(self):
        from engine.dubflow.package.render import concat_argv as package_concat
        legacy = package_concat(Path("ffmpeg"), ["a", "b"], render.VIDEO, Path("o.mp4"), encoder="x")
        legacy[legacy.index("-b:v") + 1] = "8M"
        self.assertEqual(render.concat_argv(Path("ffmpeg"), ["a", "b"], Path("o.mp4"), encoder="x"), legacy)
        copy = render.concat_copy_argv(Path("ffmpeg"), "l.ffconcat", Path("o.mp4"))
        self.assertEqual(copy[copy.index("-c") + 1], "copy")
        self.assertNotIn("-filter_complex", copy)

    def test_the_raw_join_copies_pictures_and_joins_sound_sample_exactly(self):
        argv = render.concat_copy_argv(Path("ffmpeg"), "l.ffconcat", Path("o.mp4"), parts=["p0", "p1"], durations_ms=[1000, 2017])
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn(f"atrim=end_sample={render.part_samples(1000)}", graph)
        self.assertIn(f"atrim=end_sample={render.part_samples(2017)}", graph)
        self.assertIn("setts=pts=PTS-STARTPTS", argv[argv.index("-bsf:v") + 1])

    def test_product_segments_end_on_the_grid(self):
        from engine.dubflow.product.render import Segment, Visual
        segments = [Segment(Visual(Path(f"c{k}.mp4"), 9000), 0, length) for k, length in enumerate([1237, 2011, 1550])]
        argv = product_render.ffmpeg_argv(Path("ffmpeg"), segments, Path("n.wav"), None, 0.0, 4798, Path("o.mp4"))
        graph = argv[argv.index("-filter_complex") + 1]
        for count in product_render.frame_counts([1237, 2011, 1550]):
            self.assertIn(f"trim=end_frame={count}", graph)
        self.assertNotIn("trim=duration=", graph.split("[voice]")[0])


class VerdictTests(unittest.TestCase):
    """A join that could not be measured is never counted as in sync (review of #786)."""

    def rows(self, *offsets):
        return [{"join": i, "av_ms": av} for i, av in enumerate(offsets, start=1)]

    def test_every_join_measured_and_in_sync_passes(self):
        self.assertIs(probe.verdict(self.rows(0.0, 12.5, -33.3), 1000 / 30), True)

    def test_an_unmeasured_join_is_never_a_pass(self):
        # before: True, because the two joins that were measured are in sync
        self.assertIsNone(probe.verdict(self.rows(0.0, None, 1.3), 1000 / 30))
        self.assertIsNone(probe.verdict(self.rows(None, None), 1000 / 30))
        self.assertIsNone(probe.verdict([], 1000 / 30))

    def test_a_join_out_of_sync_fails_even_beside_unmeasured_ones(self):
        self.assertIs(probe.verdict(self.rows(None, 45.0), 1000 / 30), False)  # over max(40 ms, 33.3 ms)
        self.assertIs(probe.verdict(self.rows(0.0, 41.0), 1000 / 24), True)   # within one frame at 24 fps (41.7 ms)
        self.assertIs(probe.verdict(self.rows(0.0, 45.0), 1000 / 24), False)
        self.assertIs(probe.verdict(self.rows(0.0, 90.0), 1000 / 10), False)  # a 100 ms frame, but over the 80 ms total


@unittest.skipUnless(FFMPEG and FFPROBE, "needs FFmpeg")
class PitchedVoiceTests(unittest.TestCase):
    """rc12 W4 (P2-N19): narration's pitch pulses (120–220 Hz) make the 1 ms loudness envelope correlate almost as
    well a few ms off the true lag, so the clear-peak rule rejected every real join. No FFmpeg: samples are made here."""

    @staticmethod
    def narration(ms: int, seed: int, music: float) -> list[int]:
        import random
        rnd = random.Random(seed)
        n = probe.RATE * ms // 1000
        out = [0.0] * n
        t = 0
        while t < n:  # syllables of 120–300 ms, a voiced pulse train at the syllable's pitch, gaps between them
            syllable, gap = (rnd.randint(120, 300) * probe.RATE // 1000, rnd.randint(30, 120) * probe.RATE // 1000)
            pitch, level = rnd.uniform(120, 220), rnd.uniform(3000, 9000)
            for i in range(min(syllable, n - t)):
                phase = (i * pitch / probe.RATE) % 1.0
                out[t + i] += level * math.sin(math.pi * i / syllable) * math.exp(-12 * phase) \
                    * math.sin(2 * math.pi * 900 * i / probe.RATE)
            t += syllable + gap
        for i in range(n):  # a steady music bed and a little noise
            out[i] += music * (math.sin(2 * math.pi * 220 * i / probe.RATE) + 0.7 * math.sin(2 * math.pi * 330.7 * i / probe.RATE)) \
                + rnd.gauss(0, 50)
        return [int(x) for x in out]

    def test_a_join_of_narration_is_measured_at_its_true_lag(self):
        rate = probe.RATE // 1000
        for seed, music in ((1, 0), (2, 0), (1, 300), (2, 300)):
            whole = self.narration(6000, seed, music)
            for shift in (0, 17, -23):  # where the next part's opening really is, against the plan (ms)
                join, begin = 2000, 2000 - probe.SEARCH_MS
                part = whole[join * rate:(join + probe.AUDIO_REF_MS) * rate]
                final = whole[(begin - shift) * rate:(begin - shift + probe.AUDIO_REF_MS + 2 * probe.SEARCH_MS) * rate]
                centre = join - begin
                lag, _ = probe.best_lag(probe.onsets(probe.envelope(part)), probe.onsets(probe.envelope(final)), centre,
                                        probe.SEARCH_MS, apart=probe.SIDELOBE_MS // probe.ENVELOPE_MS)
                self.assertEqual(lag, shift, (seed, music, shift))
                # the method before: the envelope itself, 1.5 s, a peak twice anything beyond 2 ms
                old, score = probe.best_lag(probe.envelope(part[:probe.REF_MS * rate]),
                                            probe.envelope(final[:(probe.REF_MS + 2 * probe.SEARCH_MS) * rate]), centre, probe.SEARCH_MS)
                if seed == 1 and music == 0:
                    self.assertIsNone(old, (shift, score))  # the true peak was there, but never clear

    def test_silence_is_never_measured(self):
        lag, _ = probe.best_lag(probe.onsets(probe.envelope([0] * 48_000)), probe.onsets(probe.envelope([0] * 76_800)),
                                300, probe.SEARCH_MS, apart=probe.SIDELOBE_MS)
        self.assertIsNone(lag)


class BoundaryTests(unittest.TestCase):
    """Decoded boundaries of real renders (FFmpeg) on synthetic markers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        (cls.dir / "empty.ass").write_text(ASS, encoding="utf-8")
        encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        cls.encoder = "libx264" if "libx264" in encoders else "mpeg4"  # B-frames when available (the join's case)
        # the blur the product uses with this FFmpeg (the shipped LGPL build has no boxblur, rc12 W4 P3-N20)
        cls.blur = render.available_blur(Path(FFMPEG)).replace("boxblur=20:2", "boxblur=2:1").replace("sigma=20", "sigma=2")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ffmpeg(self, *args):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-nostdin", *args], cwd=self.dir, check=True, capture_output=True)

    def source(self, i, rate, *, sound=True, vfr=False, texture=False):
        name = f"s-{rate.replace('/', '_')}-{i}{'-vfr' if vfr else ''}{'-mute' if not sound else ''}{'-tx' if texture else ''}.mp4"
        if (self.dir / name).exists():
            return self.dir / name
        picture = (f"testsrc2=s=320x240:r={rate}:d=40" if texture else f"color=c={NAMES[i]}:s=320x240:r={rate}:d=40")
        args = ["-f", "lavfi", "-i", picture]
        if sound:
            args += ["-f", "lavfi", "-i", (f"anoisesrc=c=pink:r=48000:a=0.3:d=40:seed={i + 1}" if texture
                                           else f"sine=f={TONES[i]}:r=48000:d=40")]
        if vfr:  # every 7th frame dropped: frames of two lengths
            args += ["-vf", "select='not(eq(mod(n\\,7)\\,3))'", "-fps_mode", "vfr"]
        args += ["-c:v", "libx264" if self.encoder == "libx264" else "mpeg4", "-pix_fmt", "yuv420p"]
        if sound:
            args += ["-c:a", "aac", "-shortest"]
        self.ffmpeg(*args, name)
        return self.dir / name

    def voice(self, lengths, name):
        out = []
        for k, length in enumerate(lengths):
            f = VOICE[k % len(VOICE)]
            out += [int(8000 * math.sin(2 * math.pi * f * (len(out) + j) / 48000)) for j in range(length * 48)]
        with wave.open(str(self.dir / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(struct.pack(f"<{len(out)}h", *out))
        return self.dir / name

    def part(self, pieces, lengths, name, narration=None):
        argv = render.montage_argv(Path(FFMPEG), pieces, narration, None, 0.0, sum(lengths), "empty.ass", self.dir / name,
                                   layout="toan-man-hinh", encoder=self.encoder, blur=self.blur, fade=False)
        subprocess.run(argv, cwd=self.dir, check=True, capture_output=True)
        return self.dir / name

    def assert_boundaries(self, found, label):
        keep(label, found)
        for row in found["rows"]:
            with self.subTest(label, boundary=row["boundary"]):
                self.assertIsNotNone(row["av_ms"], f"{label}: boundary {row['boundary']} not measured")
                self.assertLessEqual(abs(row["av_ms"]), probe.limit_ms(FRAME_MS), row)
                self.assertLessEqual(abs(row["video_ms"]), FRAME_MS / 2 + 2.5, row)  # the pictures on the plan
        self.assertTrue(found["passed"], label)
        self.assertLessEqual(found["worst_av_ms"], probe.CUMULATIVE_MS)

    def test_source_sound_stays_with_its_pictures_at_every_rate(self):
        lengths = [1237, 2011, 1550, 997, 3123, 1801]  # the 3.1 s piece is the one loudnorm used to cut short
        for rate, vfr in (("24", False), ("25", False), ("30000/1001", False), ("25", True)):
            pieces = [(self.source(i, rate, vfr=vfr), Video(320, 240, rate), 1000 + 300 * i, length, "source", True, False)
                      for i, length in enumerate(lengths)]
            out = self.part(pieces, lengths, f"part-{rate.replace('/', '_')}{'-vfr' if vfr else ''}.mp4")
            found = probe.measure_markers(FFMPEG, out, edges(lengths), COLOURS, TONES, fps=render.FPS)
            self.assert_boundaries(found, f"{rate}{' VFR' if vfr else ''}")

    def test_mixed_rates_in_one_part(self):
        lengths = [1100, 1733, 2900, 1250, 3300, 1490]
        rates = ["24", "25", "30000/1001", "30", "50", "60"]
        pieces = [(self.source(i, rates[i]), Video(320, 240, rates[i]), 500 + 410 * i, length, "source", True, False)
                  for i, length in enumerate(lengths)]
        out = self.part(pieces, lengths, "part-mixed.mp4")
        self.assert_boundaries(probe.measure_markers(FFMPEG, out, edges(lengths), COLOURS, TONES, fps=render.FPS), "mixed")

    def test_silent_sources_stills_and_short_pieces_keep_the_narration_on_the_pictures(self):
        lengths = [1400, 20, 1210, 40, 2333, 1555]  # 20 ms is less than a frame
        pieces = []
        for i, length in enumerate(lengths):
            still = i == 2
            mute = i in (0, 4)
            pieces.append((self.source(i, "25", sound=not mute), Video(320, 240, "25"), 800 + 200 * i, length, "narration",
                           not mute, still))
        out = self.part(pieces, lengths, "part-voice.mp4", narration=self.voice(lengths, "voice.wav"))
        found = probe.measure_markers(FFMPEG, out, edges(lengths), COLOURS, VOICE, fps=render.FPS)
        keep("silent, still and short pieces (narration)", found)
        for row in found["rows"]:
            planned = row["plan_ms"]
            with self.subTest(boundary=row["boundary"]):
                self.assertIsNotNone(row["video_ms"])
                self.assertIsNotNone(row["audio_ms"])
                self.assertLessEqual(abs(row["audio_ms"]), 2.5, row)  # the narration is the clock
                # a piece shorter than a frame shows one frame: its cut is late by up to a frame, the next catches up
                short = lengths[row["boundary"] - 1] < FRAME_MS
                self.assertLessEqual(abs(row["video_ms"]), (FRAME_MS if short else FRAME_MS / 2) + 2.5, (planned, row))
                self.assertLessEqual(abs(row["av_ms"]), probe.limit_ms(FRAME_MS), row)

    def test_ten_joined_parts_every_boundary_and_every_join(self):
        parts = [[1100 + 37 * k, 1500 - 23 * k, 900 + 61 * k] for k in range(10)]
        names, durations, flat = [], [], []
        for k, lengths in enumerate(parts):
            pieces = [(self.source((k + i) % 6, "25"), Video(320, 240, "25"), 1000 + 250 * i, length, "source", True, False)
                      for i, length in enumerate(lengths)]
            self.part(pieces, lengths, f"j{k}.mp4")
            names.append(f"j{k}")
            durations.append(sum(lengths))
            flat += lengths
        (self.dir / "parts.ffconcat").write_text(render.concat_list(names), encoding="utf-8")
        new = self.dir / "joined.mp4"
        subprocess.run(render.concat_copy_argv(Path(FFMPEG), "parts.ffconcat", new, parts=names, durations_ms=durations),
                       cwd=self.dir, check=True, capture_output=True)
        # every piece edge of the programme, joins included, against the plan of the whole programme
        plan = []
        at = 0
        for k, lengths in enumerate(parts):
            for length in lengths:
                at += length
                plan.append(at)
            at = sum(render.part_samples(d) for d in durations[:k + 1]) / 48  # a part is whole frames
            plan[-1] = at
        # each boundary's two neighbours have different markers ((k + i) % 6 over three pieces a part)
        found = probe.measure_markers(FFMPEG, new, plan[:-1], COLOURS, TONES, fps=render.FPS)
        self.assert_boundaries(found, "joined")

        old = self.dir / "joined-old.mp4"
        subprocess.run(render.concat_copy_argv(Path(FFMPEG), "parts.ffconcat", old), cwd=self.dir, check=True, capture_output=True)
        before = probe.measure_markers(FFMPEG, old, plan[:-1], COLOURS, TONES, fps=render.FPS)
        keep("joined, old copy join (baseline)", before)
        self.assertGreater(before["worst_av_ms"], probe.limit_ms(FRAME_MS), "the old join drifted (baseline)")

    def test_the_reference_method_measures_every_join_of_real_like_footage(self):
        parts = [[1100 + 37 * k, 1500 - 23 * k] for k in range(10)]
        names, durations = [], []
        for k, lengths in enumerate(parts):
            pieces = [(self.source((k + i) % 6, "25", texture=True), Video(320, 240, "25"), 1000 + 4000 * k + 1700 * i, length,
                       "source", True, False) for i, length in enumerate(lengths)]
            self.part(pieces, lengths, f"t{k}.mp4")
            names.append(f"t{k}")
            durations.append(sum(lengths))
        (self.dir / "tx.ffconcat").write_text(render.concat_list(names), encoding="utf-8")
        files = [(self.dir / f"{n}.mp4", render.part_samples(d) / 48) for n, d in zip(names, durations)]
        new = self.dir / "tx-new.mp4"
        subprocess.run(render.concat_copy_argv(Path(FFMPEG), "tx.ffconcat", new, parts=names, durations_ms=durations),
                       cwd=self.dir, check=True, capture_output=True)
        found = probe.measure_joins(FFMPEG, new, files, fps=render.FPS)
        keep("reference method, new join", found)
        self.assertEqual(found["unmeasured"], 0, found)
        self.assertTrue(found["passed"], found)
        for row in found["joins"]:
            self.assertLessEqual(abs(row["av_ms"]), probe.limit_ms(FRAME_MS), row)

        old = self.dir / "tx-old.mp4"
        subprocess.run(render.concat_copy_argv(Path(FFMPEG), "tx.ffconcat", old), cwd=self.dir, check=True, capture_output=True)
        before = probe.measure_joins(FFMPEG, old, files, fps=render.FPS)
        keep("reference method, old join (baseline)", before)
        self.assertIs(before["passed"], False, before)  # the old join (baseline) is measured and fails (unmeasured is None)


if __name__ == "__main__":
    unittest.main()

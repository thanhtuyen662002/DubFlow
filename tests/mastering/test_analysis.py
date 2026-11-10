"""Scene analysis (ADR-0052 §6): shots, motion and silences in one FFmpeg pass, cached."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import shots as docu_shots
from engine.dubflow.mastering import analysis as a

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def runner(calls: list):
    def run(argv: list[str], cwd: Path, cancelled) -> None:
        calls.append(argv)
        subprocess.run(argv, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return run


def thumbs(levels: list[int]) -> bytes:
    """One flat 32×18 grey thumbnail per 0.2 s sample."""
    size = docu_shots.SLOW_W * docu_shots.SLOW_H
    return b"".join(bytes([level]) * size for level in levels)


class PureTests(unittest.TestCase):
    def test_silences_are_ordered_whole_milliseconds_inside_the_source(self):
        log = ("frame:1 pts:1\nlavfi.silence_start=1.5004\nframe:2\nlavfi.silence_end=2.5\nlavfi.silence_duration=1\n"
               "lavfi.silence_start=-0.01\nlavfi.silence_end=0.4\nlavfi.silence_start=9.2\n")
        self.assertEqual(a.silences(log, 10_000), [(1500, 2500), (0, 400), (9200, 10_000)])
        self.assertEqual(a.silences("", 10_000), [])

    def test_cuts_shots_and_motion_from_ffmpeg_outputs(self):
        # 2 s steady, then 2 s changing every sample; scdet reports a hard cut at 2.0 s.
        steady, moving = [40] * 10, [40 + (i % 2) * 120 for i in range(10)]
        result = a.analyse_text("frame:60 pts:2\nlavfi.scd.score=40\nlavfi.scd.time=2.0\n", thumbs(steady + moving), None,
                                4000, "id")
        self.assertEqual(result.cuts_ms, (2000,))
        self.assertEqual([(s.start_ms, s.end_ms) for s in result.shots], [(0, 2000), (2000, 4000)])
        self.assertEqual(result.shots[0].motion, 0)
        self.assertGreater(result.shots[1].motion, 300)
        self.assertFalse(result.has_audio)
        self.assertEqual(result.shot_at(2500), result.shots[1])

    def test_no_cut_gives_one_shot_and_round_trips_through_json(self):
        result = a.analyse_text("", thumbs([10] * 20), "lavfi.silence_start=1\nlavfi.silence_end=2\n", 4000, "id")
        self.assertEqual(len(result.shots), 1)
        self.assertEqual(result.silences_ms, ((1000, 2000),))
        self.assertEqual(a.Analysis.from_dict(json.loads(json.dumps(result.to_dict()))), result)

    def test_a_cache_of_another_recipe_or_with_floats_is_not_trusted(self):
        good = a.analyse_text("", thumbs([10] * 20), None, 4000, "id").to_dict()
        for broken in ({**good, "recipe": "old"}, {**good, "duration_ms": 4000.0}, {**good, "cuts_ms": [2000, 1000]},
                       {**good, "schema_version": 2}):
            with self.subTest(broken=broken), self.assertRaises((ValueError, KeyError, TypeError)):
                a.Analysis.from_dict(broken)

    def test_the_pass_reads_audio_only_when_there_is_audio(self):
        names = dict(cut_log="c.txt", thumbs="t.gray", silence_log="s.txt")
        self.assertNotIn("0:a:0", a.argv(Path("ffmpeg"), Path("v.mp4"), has_audio=False, **names))
        with_audio = a.argv(Path("ffmpeg"), Path("v.mp4"), has_audio=True, **names)
        self.assertIn("0:a:0", with_audio)
        self.assertEqual(with_audio.count("-i"), 1)  # one decode for all three


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class MediaTests(unittest.TestCase):
    def make(self, folder: Path, name: str, *, audio: bool, portrait: bool = False) -> Path:
        """2 s of a still colour, a hard cut, 2 s of moving test pattern; audio: tone, 1 s silence at 1.5 s, tone."""
        size = "180x320" if portrait else "320x180"
        video = folder / name
        graph = (f"color=c=0x3060A0:s={size}:r=25:d=2[a];testsrc2=s={size}:r=25:d=2[b];[a][b]concat=n=2:v=1:a=0[v]")
        command = [FFMPEG, "-v", "error", "-y"]
        if audio:
            for source in ("sine=f=440:r=48000:d=1.5", "anullsrc=r=48000:cl=mono:d=1", "sine=f=440:r=48000:d=1.5"):
                command += ["-f", "lavfi", "-i", source]
            graph += ";[0:a][1:a][2:a]concat=n=3:v=0:a=1[au]"
            command += ["-filter_complex", graph, "-map", "[v]", "-map", "[au]", "-c:a", "aac"]
        else:
            command += ["-filter_complex", graph, "-map", "[v]"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "4", str(video)]
        subprocess.run(command, check=True, timeout=120)
        return video

    def test_one_pass_finds_the_cut_the_motion_and_the_silence_then_caches(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            video = self.make(root, "clip.mp4", audio=True)
            info = a.probe(Path(FFPROBE), video)
            self.assertTrue(info.has_audio)
            self.assertEqual((info.width, info.height), (320, 180))
            calls: list = []
            cache = root / "work" / "analysis" / "clip.json"
            result = a.analyse(video, info, cache, ffmpeg=Path(FFMPEG), run=runner(calls))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(result.cuts_ms), 1)
            self.assertLess(abs(result.cuts_ms[0] - 2000), 100)
            still, moving = result.shots
            self.assertLess(still.motion, 20)
            self.assertGreater(moving.motion, still.motion + 20)
            self.assertTrue(any(abs(start - 1500) < 150 and abs(end - 2500) < 150 for start, end in result.silences_ms),
                            result.silences_ms)
            self.assertEqual(sorted(p.name for p in cache.parent.iterdir()), ["clip.json"])  # temporary files removed
            again = a.analyse(video, info, cache, ffmpeg=Path(FFMPEG), run=runner(calls))
            self.assertEqual(len(calls), 1)  # from the cache
            self.assertEqual(again, result)

    def test_a_silent_portrait_clip_has_no_silences_to_report(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            video = self.make(root, "tall.mp4", audio=False, portrait=True)
            info = a.probe(Path(FFPROBE), video)
            self.assertFalse(info.has_audio)
            self.assertEqual((info.width, info.height), (180, 320))
            result = a.analyse(video, info, root / "a" / "tall.json", ffmpeg=Path(FFMPEG), run=runner([]))
            self.assertFalse(result.has_audio)
            self.assertEqual(result.silences_ms, ())
            self.assertEqual(result.shots[0].start_ms, 0)
            self.assertEqual(result.shots[-1].end_ms, info.duration_ms)


if __name__ == "__main__":
    unittest.main()

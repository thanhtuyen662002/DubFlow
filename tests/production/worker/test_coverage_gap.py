from __future__ import annotations

import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.worker import production_job as worker


def tone_wav(path: Path, seconds: list[bool], rate: int = 16000) -> Path:
    """One second of a 440 Hz tone (-12 dBFS) for each True, silence for each False."""
    with wave.open(str(path), "wb") as writer:
        writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        for loud in seconds:
            amplitude = 8000 if loud else 0
            writer.writeframes(b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate)))
    return path


class CoverageGapTests(unittest.TestCase):
    def test_audible_audio_without_cues_is_reported(self):
        # 2026-11-04: speech was recognised for 0–46 s of a 118 s clip; the rest (audible) had no cue and QC passed.
        with TemporaryDirectory() as temp:
            audio = tone_wav(Path(temp) / "analysis.wav", [True] * 60)
            loud = worker._loud_seconds(audio)
            self.assertEqual(loud, [True] * 60)
            cues = [worker.TextCue("a", 0, 10_000, "你好"), worker.TextCue("b", 12_000, 20_000, "你好")]
            self.assertEqual(worker._coverage_gaps(cues, loud), [(20, 60)])

    def test_silence_short_gaps_and_full_coverage_are_fine(self):
        cues = [worker.TextCue("a", 0, 10_000, "你好"), worker.TextCue("b", 22_000, 30_000, "你好")]
        self.assertEqual(worker._coverage_gaps(cues, [True] * 30), [], "a 12 s gap is shorter than the threshold")
        self.assertEqual(worker._coverage_gaps(cues, [True] * 30 + [False] * 40), [], "a silent tail is not a gap")
        self.assertEqual(worker._coverage_gaps([], [False] * 10 + [True] * 30), [(0, 40)], "no cues at all over mostly loud audio")
        self.assertEqual(worker._coverage_gaps([], [False] * 30 + [True] * 10), [], "mostly silent")


if __name__ == "__main__":
    unittest.main()

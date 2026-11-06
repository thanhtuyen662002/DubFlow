from __future__ import annotations

from array import array
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.translation.sensitive import SensitivePolicy
from engine.dubflow.worker import beep

RATE = 16000


def write(path: Path, seconds: float, voiced: tuple[float, float], channels: int = 1) -> None:
    samples = array("h")
    for frame in range(int(seconds * RATE)):
        value = int(8000 * math.sin(2 * math.pi * 220 * frame / RATE)) if voiced[0] <= frame / RATE < voiced[1] else 0
        samples.extend([value] * channels)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(samples.tobytes())


def read(path: Path) -> array:
    with wave.open(str(path), "rb") as reader:
        samples = array("h")
        samples.frombytes(reader.readframes(reader.getnframes()))
    return samples


class BeepTests(unittest.TestCase):
    def test_the_word_is_found_by_its_syllables_inside_the_voiced_span(self):
        policy = SensitivePolicy.from_mapping({"mode": "beep", "words": [{"from": "giết", "to": "giớt"}]})
        text = "anh ta giết rồi"
        with TemporaryDirectory() as temp:
            stem = Path(temp) / "stem.wav"
            write(stem, 3.0, (1.0, 2.0))
            spans = beep.beep_spans([{"id": "p1", "start": 0.5, "end": 2.8}], {"p1": text}, {"p1": policy.matches(text)}, stem)
        self.assertEqual(len(spans), 1)
        start, end = spans[0]
        self.assertAlmostEqual(start, 1.5 - beep.PAD_SECONDS, delta=0.03)
        self.assertAlmostEqual(end, 1.75 + beep.PAD_SECONDS, delta=0.03)

    def test_close_words_merge_and_only_the_spans_change(self):
        with TemporaryDirectory() as temp:
            source, target = Path(temp) / "mix.wav", Path(temp) / "out.wav"
            write(source, 2.0, (0.0, 2.0), channels=2)
            beep.write_beeped(source, target, [(0.5, 0.8)], block_frames=1000)
            before, after = read(source), read(target)
        self.assertEqual(len(before), len(after))
        self.assertEqual(before[:int(0.5 * RATE) * 2], after[:int(0.5 * RATE) * 2])
        self.assertEqual(before[int(0.8 * RATE) * 2:], after[int(0.8 * RATE) * 2:])
        middle = int(0.65 * RATE) * 2
        self.assertNotEqual(before[middle:middle + 200], after[middle:middle + 200])

    def test_fractions(self):
        self.assertEqual(beep.word_fractions("một hai, ba bốn", [(9, 11, "ba")]), [(0.5, 0.75)])
        self.assertEqual(beep.word_fractions("", [(0, 1, "x")]), [])


if __name__ == "__main__":
    unittest.main()

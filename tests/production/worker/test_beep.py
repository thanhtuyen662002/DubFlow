from __future__ import annotations

from array import array
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.translation.sensitive import SensitivePolicy
from dataclasses import dataclass
import json

from engine.dubflow.worker import beep, production_job

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



@dataclass(frozen=True)
class SpokenCue:
    cue_id: str
    start_ms: int
    end_ms: int
    translated_text: str


@dataclass(frozen=True)
class FakeB2Audio:
    final_mix_path: Path
    dialogue_stem_path: Path
    placement_document_path: Path
    member_cue_ids: dict


def point(seconds: float) -> dict:
    # As the TTS placement writes it: ticks and the time base are decimal strings.
    return {"kind": "time_point", "schema_version": 1, "ticks": str(int(seconds * 1000)), "time_base": {"numerator": "1", "denominator": "1000"}}


class BeepFlaggedWordsTests(unittest.TestCase):
    def test_a_real_placement_document_gives_beeped_copies(self):
        policy = SensitivePolicy.from_mapping({"mode": "beep", "words": [{"from": "máu", "to": "máo"}]})
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            stem, mix, placement = folder / "dialogue_stem.wav", folder / "final_mix.wav", folder / "dubbing_placement.json"
            write(stem, 3.0, (0.5, 2.0))
            write(mix, 3.0, (0.5, 2.0))
            placement.write_text(json.dumps({"windows": [{"cue_id": "phrase-1", "slot_start": point(0.4), "render_window_end": point(2.2)}]}), encoding="utf-8")
            audio = FakeB2Audio(mix, stem, placement, {"phrase-1": ("c1",)})
            warnings: list[str] = []
            result = production_job._beep_flagged_words(audio, [SpokenCue("c1", 400, 2200, "chảy máu mũi rồi")], policy, warnings)
            self.assertIsNotNone(result, warnings)
            self.assertTrue(any(warning.startswith("SENSITIVE_WORDS_BEEPED:") for warning in warnings), warnings)
            self.assertFalse(any("BEEP_SKIPPED" in warning for warning in warnings), warnings)
            final, beeped_stem = result
            self.assertNotEqual(read(final), read(mix))
            self.assertNotEqual(read(beeped_stem), read(stem))

    def test_placement_seconds_reads_string_ticks(self):
        self.assertAlmostEqual(production_job._placement_seconds(point(8.275)), 8.275)


if __name__ == "__main__":
    unittest.main()

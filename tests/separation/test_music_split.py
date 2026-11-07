"""Nhạc and SFX: the accompaniment split into a pitched part and a short/broadband part that add back up."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

import numpy as np

from engine.dubflow.separation import split

RATE = 16000


def write(path: Path, data: np.ndarray) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(data.shape[1])
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(np.clip(np.round(data * 32767), -32768, 32767).astype("<i2").tobytes())


def read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as reader:
        return np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(np.int64).reshape(-1, reader.getnchannels())


def corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b))


class MusicSplitTest(unittest.TestCase):
    def test_a_tone_is_music_clicks_are_sfx_and_they_add_back_up(self):
        count = RATE * 45  # three blocks: the block seams must not show
        t = np.arange(count) / RATE
        tone = 0.2 * np.sin(2 * np.pi * 440 * t)
        clicks = np.zeros(count)
        for start in range(0, count, RATE // 2):
            clicks[start:start + 200] += 0.3 * np.random.RandomState(start).randn(200) * np.exp(-np.arange(200) / 40)
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.stack([tone + clicks, tone + clicks], axis=1))
            source = folder / "source.wav"
            voice = 0.1 * np.sin(2 * np.pi * 180 * t)
            write(source, np.stack([tone + clicks + voice, tone + clicks + voice], axis=1))
            seen = []
            document = split.split(background, folder, source=source, progress=seen.append)
            spoken = read(folder / "voice.wav")
            music, sfx, whole = read(folder / "music.wav"), read(folder / "sfx.wav"), read(background)
            saved = json.loads((folder / split.SPLIT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(music.shape, whole.shape)
        self.assertLessEqual(int(np.abs(music + sfx - whole).max()), 2)
        self.assertGreater(corr(music[:, 0] / 32768, tone), 0.99)
        self.assertGreater(corr(sfx[:, 0] / 32768, clicks), 0.95)
        self.assertEqual(seen[-1], 1.0)
        self.assertGreater(corr(spoken[:, 0] / 32768, voice), 0.99)
        self.assertEqual(saved, document)
        self.assertEqual(saved["recipe"], split.RECIPE)

    def test_a_short_mono_file_and_a_bad_format(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            short = folder / "short.wav"
            write(short, np.full((100, 1), 0.1))
            split.split(short, folder)
            self.assertEqual(read(folder / "music.wav").shape, (100, 1))
            bad = folder / "bad.wav"
            with wave.open(str(bad), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(1)
                writer.setframerate(RATE)
                writer.writeframes(b"\x80" * 100)
            with self.assertRaises(split.SplitError):
                split.split(bad, folder)

    def test_cancel_leaves_no_parts(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.zeros((RATE, 2)))
            with self.assertRaises(split.SplitError):
                split.split(background, folder / "out", cancelled=lambda: True)
            self.assertEqual(sorted(path.name for path in (folder / "out").iterdir()), [])


if __name__ == "__main__":
    unittest.main()

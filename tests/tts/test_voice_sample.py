"""Nghe thử: one voice reads a short line into a WAV."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.tts import sample


class FakeSpeaker:
    sample_rate = 48000
    closed = False

    def speak(self, text):
        self.text = text
        return [0.0, 0.5, -0.5] * 1000

    def close(self):
        FakeSpeaker.closed = True


class VoiceSampleTest(unittest.TestCase):
    def test_writes_the_line_as_a_wav(self):
        with TemporaryDirectory() as temp:
            out = Path(temp) / "samples" / "vi-a-v1.wav"
            speaker = FakeSpeaker()
            sample.make_sample("vi-a", out, app_root=Path(temp), model_root=Path(temp), ffmpeg=Path("ffmpeg"),
                               speaker_factory=lambda: speaker)
            self.assertIn("DubFlow", speaker.text)
            self.assertTrue(FakeSpeaker.closed)
            with wave.open(str(out), "rb") as reader:
                self.assertEqual((reader.getnchannels(), reader.getframerate()), (1, 48000))
                self.assertGreater(reader.getnframes(), 3000)

    def test_failure_is_reported(self):
        with TemporaryDirectory() as temp:
            code = sample.main(["--voice-id", "vi-unknown", "--out", str(Path(temp) / "x.wav"), "--app-root", temp,
                                "--model-root", temp, "--ffmpeg", "ffmpeg"])
            self.assertEqual(code, 1)
            self.assertFalse((Path(temp) / "x.wav").exists())


if __name__ == "__main__":
    unittest.main()

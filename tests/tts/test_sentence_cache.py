"""Per-sentence narration cache (engine.dubflow.tts.sentence_cache)."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from engine.dubflow.product import render
from engine.dubflow.tts import sentence_cache as sc

IDENTITY = {"engine": "fake", "profile_sha256": "a" * 64, "recipe": "r1", "voice_id": "v", "speed": 1.0}


class Fake:
    sample_rate = 48000

    def __init__(self, log):
        self.log = log
        log.append("start")

    def speak(self, text):
        self.log.append(text)
        return [((i * 37 + len(text)) % 200 - 100) / 101 for i in range(480 + len(text))]

    def close(self):
        self.log.append("close")


class SentenceCacheTests(unittest.TestCase):
    def speaker(self, root, log, identity=IDENTITY):
        return sc.CachedSpeaker(lambda: Fake(log), sc.SentenceCache(root, identity, 48000))

    def test_a_rerun_speaks_nothing_and_never_starts_the_model(self):
        with tempfile.TemporaryDirectory() as root:
            log = []
            first = self.speaker(root, log)
            a = [first.speak(t) for t in ("Câu một.", "Câu hai.")]
            first.close()
            self.assertEqual(log, ["start", "Câu một.", "Câu hai.", "close"])
            log.clear()
            again = self.speaker(root, log)
            b = [again.speak(t) for t in ("Câu một.", "Câu hai.")]
            again.close()
            self.assertEqual(log, [])
            self.assertEqual((again.cache.hits, again.cache.misses), (2, 0))
            # The narration written from cached audio is byte-identical to the fresh one.
            one, two = Path(root) / "a.wav", Path(root) / "b.wav"
            render.write_narration(one, a, 48000, lead_ms=100, gap_ms=200, tail_ms=300)
            render.write_narration(two, b, 48000, lead_ms=100, gap_ms=200, tail_ms=300)
            self.assertEqual(one.read_bytes(), two.read_bytes())

    def test_only_the_edited_sentence_is_spoken_again(self):
        with tempfile.TemporaryDirectory() as root:
            log = []
            self.speaker(root, log).speak("Câu một.")
            log.clear()
            speaker = self.speaker(root, log)
            speaker.speak("Câu một.")
            speaker.speak("Câu một đã sửa.")
            self.assertEqual(log, ["start", "Câu một đã sửa."])

    def test_another_voice_model_or_speed_never_reuses_audio(self):
        with tempfile.TemporaryDirectory() as root:
            self.speaker(root, []).speak("Câu một.")
            for change in ({"voice_id": "w"}, {"profile_sha256": "b" * 64}, {"recipe": "r2"}, {"speed": 1.1}):
                log = []
                self.speaker(root, log, {**IDENTITY, **change}).speak("Câu một.")
                self.assertEqual(log, ["start", "Câu một."], change)

    def test_a_cut_off_file_is_spoken_again(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.speaker(root, [])
            first.speak("Câu một.")
            path = first.cache.path("Câu một.")
            path.write_bytes(path.read_bytes()[:-100])
            log = []
            self.speaker(root, log).speak("Câu một.")
            self.assertEqual(log, ["start", "Câu một."])

    def test_the_cache_keeps_to_its_limit(self):
        with tempfile.TemporaryDirectory() as root:
            cache = sc.SentenceCache(root, IDENTITY, 48000, limit_bytes=3000)
            for n in range(5):
                cache.put(f"Câu {n}.", [0.1] * 1000)
            cache.prune()
            total = sum(p.stat().st_size for p in Path(root).glob("??/*.wav"))
            self.assertLessEqual(total, 3000)
            self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()

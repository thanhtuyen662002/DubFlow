from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.tts import DeterministicFixtureEngine, LocalTtsAdapter, TtsConfig, TtsError, TtsStageError, approved_default_voice
from engine.dubflow.worker.tts_cache import CachedSynthesisEngine

from test_adapter import make_provenance, segment

IDENTITY = "a" * 64


class CountingEngine:
    def __init__(self) -> None:
        self.fixture = DeterministicFixtureEngine()
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def capabilities(self):
        return self.fixture.capabilities()

    def healthcheck(self, voice):
        return self.fixture.healthcheck(voice)

    def synthesize(self, request):
        self.calls.append(request.segment.segment_id)
        if request.segment.segment_id in self.fail:
            raise TtsError("DURATION_FIT_REQUIRED", "fixture failure")
        return self.fixture.synthesize(request)


def run(engine, cues, output: Path, *, input_hash: str):
    config = TtsConfig(max_attempts=1)
    voice = approved_default_voice()
    adapter = LocalTtsAdapter(engine, config=config, voice=voice, output_dir=output,
                              provenance=replace(make_provenance(config, voice), input_hash=input_hash))
    return adapter.synthesize(cues, input_hash=input_hash)


class SynthesisCacheTests(unittest.TestCase):
    def test_new_generation_resynthesises_only_changed_cues(self):
        cues = (segment("a", 0, 1000, "Xin chào"), segment("b", 1000, 2000, "Cảm ơn"), segment("c", 2000, 3000, "Tạm biệt"))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inner = CountingEngine()
            first = run(CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY), cues, root / "gen1", input_hash="sha256:" + "1" * 64)
            self.assertEqual(inner.calls, ["a", "b", "c"])
            edited = (cues[0], replace(cues[1], text="Cảm ơn nhé"), cues[2])
            cached = CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY)
            second = run(cached, edited, root / "gen2", input_hash="sha256:" + "2" * 64)
            self.assertEqual(inner.calls, ["a", "b", "c", "b"])
            self.assertEqual(cached.hits, 2)
            # Reused waveforms are re-validated and re-published by the adapter.
            self.assertEqual(first.artifacts[0].content_hash, second.artifacts[0].content_hash)
            self.assertTrue(Path(second.artifacts[0].path).is_relative_to(root / "gen2"))

    def test_identity_voice_or_timing_change_misses(self):
        cue = segment("a", 0, 1000, "Xin chào")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inner = CountingEngine()
            run(CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY), (cue,), root / "g1", input_hash="sha256:" + "1" * 64)
            run(CachedSynthesisEngine(inner, root / "cache", identity="b" * 64), (cue,), root / "g2", input_hash="sha256:" + "1" * 64)
            run(CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY), (replace(cue, end=cue.end.__class__(1200, cue.end.time_base)),), root / "g3", input_hash="sha256:" + "1" * 64)
            self.assertEqual(inner.calls, ["a", "a", "a"])

    def test_failures_are_not_cached_and_corrupt_entries_are_ignored(self):
        cue = segment("a", 0, 1000, "Xin chào")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inner = CountingEngine()
            inner.fail = {"a"}
            with self.assertRaises(TtsStageError):
                run(CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY), (cue,), root / "g1", input_hash="sha256:" + "1" * 64)
            self.assertEqual(list((root / "cache").glob("*.json")), [])
            inner.fail = set()
            run(CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY), (cue,), root / "g2", input_hash="sha256:" + "1" * 64)
            for wav in (root / "cache").glob("*.wav"):
                wav.write_bytes(b"corrupt")
            cached = CachedSynthesisEngine(inner, root / "cache", identity=IDENTITY)
            run(cached, (cue,), root / "g3", input_hash="sha256:" + "1" * 64)
            self.assertEqual(cached.hits, 0)
            self.assertEqual(inner.calls, ["a", "a", "a"])


if __name__ == "__main__":
    unittest.main()

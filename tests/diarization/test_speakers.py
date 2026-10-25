from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.diarization import speakers as sp

try:
    import numpy as np
except ImportError:
    np = None


class PlanningTests(unittest.TestCase):
    def test_import_and_planning_without_site_packages_or_numpy(self):
        script = """
import sys
from engine.dubflow.diarization.speakers import cluster, cast_voices, gender_guess
assert 'numpy' not in sys.modules
assert cluster([[1, 0], [2, 0], [0, 1]]) == [0, 0, 1]
assert gender_guess(None) == 'unknown'
assert cast_voices([{'id': 'S1'}], [{'voice_id': 'v'}], 'v', {}) == {'S1': 'v'}
"""
        result = subprocess.run([sys.executable, "-B", "-S", "-c", script], cwd=Path(__file__).resolve().parents[2], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_cosine_average_linkage_and_deterministic_ties(self):
        self.assertEqual(sp.cluster([]), [])
        self.assertEqual(sp.cluster([[2, 0], [1, 0], [0, 1], [0, 3]]), [0, 0, 1, 1])
        angles = [0, 20, 40]
        vectors = [[math.cos(math.radians(a)), math.sin(math.radians(a))] for a in angles]
        labels = sp.cluster(vectors, math.cos(math.radians(30)))
        self.assertEqual(len(set(labels)), 2)  # single linkage would join all three
        for _ in range(3):
            self.assertEqual(sp.cluster(vectors, math.cos(math.radians(30))), labels)
        for invalid in ([[0, 0]], [[float("nan"), 1]], [[1, 0], [1]]):
            with self.assertRaises(ValueError):
                sp.cluster(invalid)

    def test_pitch_gender_boundaries(self):
        for f0, expected in ((None, "unknown"), (120, "male"), (154.9, "male"), (155, "unknown"),
                             (175, "unknown"), (175.1, "female"), (220, "female"), (float("nan"), "unknown")):
            self.assertEqual(sp.gender_guess(f0), expected)

    def test_casting_gender_determinism_and_no_reuse(self):
        catalog = [{"voice_id": "primary", "gender": "Nữ"}, {"voice_id": "m1", "gender": "Nam"},
                   {"voice_id": "f1", "gender": "Nữ"}, {"voice_id": "m2", "gender": "Nam"}]
        speakers = [{"id": "S1", "gender": "male"}, {"id": "S2", "gender": "male"},
                    {"id": "S3", "gender": "female"}, {"id": "S4", "gender": "unknown"}]
        expected = {"S1": "primary", "S2": "m1", "S3": "f1", "S4": "m2"}
        self.assertEqual(sp.cast_voices(speakers, catalog, "primary", {}), expected)
        self.assertEqual(sp.cast_voices(list(reversed(speakers)), catalog, "primary", {}), expected)
        self.assertEqual(len(set(expected.values())), 4)
        # Reserve a later override before the automatic S2 assignment.
        cast = sp.cast_voices(speakers, catalog, "primary", {"S4": "m1", "S3": "missing"})
        self.assertEqual(cast, {"S1": "primary", "S2": "m2", "S3": "f1", "S4": "m1"})
        self.assertEqual(sp.cast_voices(speakers, catalog, "primary", {"S1": "m1"})["S1"], "m1")
        # Explicit duplicates win; automatic casting still consumes unused voices.
        cast = sp.cast_voices(speakers, catalog, "primary", {"S1": "m1", "S2": "m1"})
        self.assertEqual(cast["S1"], cast["S2"])
        self.assertNotEqual(cast["S3"], "m1")
        exhausted = sp.cast_voices(speakers, catalog[:1], "primary", {})
        self.assertEqual(set(exhausted.values()), {"primary"})


@unittest.skipIf(np is None, "numpy is optional in the planning CI runtime")
class SignalTests(unittest.TestCase):
    def make_audio(self, root, duration_ms, *, raw=False):
        path = root / ("audio.pcm" if raw else "audio.wav")
        data = bytes(duration_ms * 16 * 2)
        if raw:
            path.write_bytes(data)
        else:
            with wave.open(str(path), "wb") as writer:
                writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(data)
        return path

    def cue(self, identifier, start, end):
        return {"cue_id": identifier, "start_ms": start, "end_ms": end}

    def test_features_mean_normalization_and_unit_embedding(self):
        samples = .2 * np.sin(2 * np.pi * 120 * np.arange(16000) / 16000)
        features = sp.fbank(samples)
        self.assertEqual(features.shape, (98, 80))
        self.assertEqual(features.dtype, np.float32)
        self.assertTrue(np.isfinite(features).all())
        self.assertEqual(sp.fbank(samples[:100]).shape, (0, 80))
        class Session:
            def run(inner, names, feeds):
                self.assertEqual(feeds["x"].shape, (1, 98, 80))
                np.testing.assert_allclose(feeds["x"].mean(1), 0, atol=2e-5)
                return [np.array([[3, 4]], dtype=np.float32)]
        np.testing.assert_allclose(sp.embed(Session(), samples), [.6, .8], atol=1e-6)

    def test_pitch_sine_harmonics_silence_and_noise(self):
        t = np.arange(16000) / 16000
        for frequency in (60, 90, 120, 165, 220, 330, 400):
            for harmonic in (False, True):
                with self.subTest(frequency=frequency, harmonic=harmonic):
                    signal = .15 * np.sin(2 * np.pi * frequency * t)
                    if harmonic:
                        signal += .20 * np.sin(2 * np.pi * 2 * frequency * t) + .07 * np.sin(2 * np.pi * 3 * frequency * t)
                    self.assertAlmostEqual(sp.estimate_pitch_hz(signal), frequency, delta=3)
        self.assertIsNone(sp.estimate_pitch_hz(np.zeros(16000)))
        self.assertIsNone(sp.estimate_pitch_hz(np.random.default_rng(7).normal(0, .1, 16000)))

    def test_short_cues_borrow_nearest_and_numbering_includes_their_time(self):
        cues = [self.cue("a", 0, 1000), self.cue("short-a", 1050, 1350),
                self.cue("short-b", 2700, 2900), self.cue("b", 3000, 5000)]
        for raw in (False, True):
            with self.subTest(raw=raw), TemporaryDirectory() as directory, patch.object(sp, "_load_session"), \
                 patch.object(sp, "embed", side_effect=[[1, 0], [0, 1]]) as embed, \
                 patch.object(sp, "estimate_pitch_hz", side_effect=[120, 220]):
                root = Path(directory)
                result = sp.assign_speakers(self.make_audio(root, 5000, raw=raw), cues, "unused", threshold=.3)
                self.assertEqual(embed.call_count, 2)
                self.assertEqual(result.labels, {"b": "S1", "short-b": "S1", "a": "S2", "short-a": "S2"})
                self.assertEqual(result.speakers[0], {"id": "S1", "cues": ["short-b", "b"], "seconds": 2.2, "gender": "female", "f0_hz": 220.0})

    def test_all_short_cues_and_empty_input_need_no_model(self):
        with TemporaryDirectory() as directory, patch.object(sp, "_load_session") as load:
            root = Path(directory)
            audio = self.make_audio(root, 1000)
            result = sp.assign_speakers(audio, [self.cue("a", 0, 400), self.cue("b", 500, 900)], "unused")
            self.assertEqual(result.labels, {"a": "S1", "b": "S1"})
            self.assertEqual(result.speakers[0]["gender"], "unknown")
            self.assertEqual(sp.assign_speakers(audio, [], "unused").speakers, [])
            load.assert_not_called()

    def test_speaker_cap_merges_smallest_into_most_similar(self):
        vectors = list(np.eye(9))
        vectors[8] = .8 * vectors[0] + .6 * vectors[8]
        cues = [self.cue(str(i), i * 1000, i * 1000 + (800 if i == 8 else 1000)) for i in range(9)]
        with TemporaryDirectory() as directory, patch.object(sp, "_load_session"), \
             patch.object(sp, "embed", side_effect=vectors), patch.object(sp, "estimate_pitch_hz", return_value=None):
            root = Path(directory)
            result = sp.assign_speakers(self.make_audio(root, 9000), cues, "unused", threshold=.99)
            self.assertEqual(len(result.speakers), 8)
            self.assertEqual(result.labels["8"], result.labels["0"])
            self.assertEqual(result.labels["0"], "S1")

    def test_interrupted_embeddings_resume_per_cue(self):
        cues = [self.cue("a", 0, 1000), self.cue("b", 1000, 2000)]
        with TemporaryDirectory() as directory, patch.object(sp, "_load_session"), \
             patch.object(sp, "estimate_pitch_hz", return_value=None):
            root = Path(directory)
            audio = self.make_audio(root, 2000)
            with patch.object(sp, "embed", side_effect=[[1, 0], KeyboardInterrupt()]):
                with self.assertRaises(KeyboardInterrupt):
                    sp.assign_speakers(audio, cues, "unused", checkpoint_dir=root / "chunks")
            self.assertEqual(len(list((root / "chunks").glob("*.json"))), 1)
            with patch.object(sp, "embed", return_value=[0, 1]) as embed:
                result = sp.assign_speakers(audio, cues, "unused", checkpoint_dir=root / "chunks")
            self.assertEqual(embed.call_count, 1)
            self.assertEqual(len(result.speakers), 2)
            # A changed intermediate record must not authorize embedding reuse.
            first = next(path for path in (root / "chunks").glob("*.json")
                         if json.loads(path.read_text())["vector"] == [1.0, 0.0])
            invalid = json.loads(first.read_text())
            invalid["vector"] = [0, 1]
            first.write_text(json.dumps(invalid))
            with patch.object(sp, "embed", return_value=[1, 0]) as embed:
                repaired = sp.assign_speakers(audio, cues, "unused", checkpoint_dir=root / "chunks")
            self.assertEqual(embed.call_count, 1)
            self.assertEqual(len(repaired.speakers), 2)

    @unittest.skipUnless(all(os.environ.get(key) for key in ("DUBFLOW_TEST_SPEAKER_MODEL", "DUBFLOW_TEST_FFMPEG", "DUBFLOW_TEST_SPEAKER_AUDIO")),
                         "requires local model, app ffmpeg and recorded speech WAV")
    def test_gated_recorded_audio_runs_real_model(self):
        with TemporaryDirectory() as directory:
            decoded = Path(directory) / "decoded.wav"
            subprocess.run([os.environ["DUBFLOW_TEST_FFMPEG"], "-v", "error", "-nostdin", "-i",
                os.environ["DUBFLOW_TEST_SPEAKER_AUDIO"], "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(decoded)],
                check=True, capture_output=True, timeout=120)
            with wave.open(str(decoded), "rb") as reader:
                duration = reader.getnframes() * 1000 // reader.getframerate()
            self.assertGreaterEqual(duration, 800)
            cues = [self.cue(str(start), start, min(start + 2000, duration)) for start in range(0, duration, 2000)]
            result = sp.assign_speakers(decoded, cues, os.environ["DUBFLOW_TEST_SPEAKER_MODEL"], threshold=.3)
            self.assertGreaterEqual(len(result.speakers), 1)


if __name__ == "__main__":
    unittest.main()

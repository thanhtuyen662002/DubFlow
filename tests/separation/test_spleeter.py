from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import wave

from engine.dubflow.models.runtime import load_profile
from engine.dubflow.separation import spleeter as sep
from engine.dubflow.separation.production import SeparationError

ROOT = Path(__file__).resolve().parents[2]
HAS_NUMPY = importlib.util.find_spec("numpy") is not None
FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")


class ImportAndManifestTests(unittest.TestCase):
    def test_import_does_not_require_numpy_or_onnxruntime(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('numpy', 'onnxruntime'):
        raise AssertionError('eager scientific import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from engine.dubflow.separation.spleeter import separate_background
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_pins_the_unchanged_pair_and_notice(self):
        path = ROOT / "models/manifests/production-separation-spleeter-v1.json"
        profile, artifacts = load_profile(path)
        self.assertEqual(profile, "dubflow-spleeter-2stems-v1")
        self.assertEqual([item.size_bytes for item in artifacts], [39318336, 39318343, 782])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["distribution"], "download-on-opt-in")
        self.assertTrue(all(item.relative_path.startswith(data["model_dir"] + "/") for item in artifacts))
        self.assertIn("2019-present Deezer SA", (ROOT / "docs/licenses/spleeter.md").read_text())


@unittest.skipUnless(HAS_NUMPY, "numpy is optional in deterministic CI")
class SpleeterMathTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np

    def signal(self, length):
        np = self.np
        rng = np.random.default_rng(171)
        return rng.normal(0, .08, (2, length)).astype(np.float32)

    def sessions(self):
        return (Mock(run=Mock(side_effect=lambda _, feed: [feed["x"] * .5])),
                Mock(run=Mock(side_effect=lambda _, feed: [feed["x"] * .5])))

    def separate(self, signal, *, splits=1, sessions=None, **kwargs):
        return self.np.concatenate(list(sep._separated_blocks(
            lambda start, count: signal[:, start:start + count], signal.shape[1],
            lambda: sessions or self.sessions(), batch_splits=splits, **kwargs)), axis=1)

    def test_identity_stft_istft_reconstructs_edges_and_arbitrary_lengths(self):
        for length in (1, 1024, 5137, 20101):
            with self.subTest(length=length):
                signal = self.signal(length)
                self.np.testing.assert_allclose(sep._istft(sep._stft(signal), length), signal, atol=6e-8)

    def test_ratio_masks_include_epsilon_and_extend_with_per_frame_mean(self):
        np = self.np
        self.np.testing.assert_allclose(sep._accompaniment_mask(np.array([0., 1., 3.]), np.array([0., 2., 4.])), [.5, .8, .64])
        x = self.signal(9000)
        actual = self.separate(x)
        self.np.testing.assert_allclose(actual, x * .5, atol=6e-8)

    def test_frequency_dependent_mask_matches_spike_average_high_bin_extension(self):
        np = self.np
        x = self.signal(9201)
        frequencies = np.linspace(.1, .9, sep.BINS, dtype=np.float32)
        models = (Mock(run=Mock(side_effect=lambda _, feed: [feed["x"] * frequencies])),
                  Mock(run=Mock(side_effect=lambda _, feed: [feed["x"] * (1 - frequencies)])))
        actual = self.separate(x, sessions=models)
        spec = sep._stft(x)
        magnitude = np.abs(spec[:, :, :sep.BINS]).astype(np.float32)
        vocals, accompaniment = magnitude * frequencies, magnitude * (1 - frequencies)
        mask = (accompaniment ** 2 + 5e-11) / (vocals ** 2 + accompaniment ** 2 + 1e-10)
        full = np.concatenate((mask, np.repeat(mask.mean(-1, keepdims=True), 1025, axis=-1)), axis=-1)
        np.testing.assert_array_equal(actual, sep._istft(spec * full, x.shape[1]))

    def test_batching_matches_single_pass_including_partial_final_split(self):
        x = self.signal(sep.HOP * (sep.SPLIT + 7) + 31)
        sessions = self.sessions()
        batched = self.separate(x, sessions=sessions)
        single = self.separate(x, splits=8)
        self.np.testing.assert_array_equal(batched, single)
        self.np.testing.assert_allclose(batched, x * .5, atol=6e-8)
        for session in sessions:
            self.assertEqual(session.run.call_count, 2)
            self.assertTrue(all(call.args[1]["x"].shape == (2, 1, 512, 1024) for call in session.run.call_args_list))

    def test_completed_batches_resume_without_inference_and_corruption_recomputes(self):
        x = self.signal(sep.HOP * (sep.SPLIT + 5) + 17)
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            batches = sep._separated_blocks(lambda start, count: x[:, start:start + count], x.shape[1],
                self.sessions, batch_splits=1, checkpoint_dir=cache)
            next(batches)  # Simulate a crash after the first committed batch.
            batches.close()
            sessions = self.sessions()
            resumed = self.separate(x, sessions=sessions, checkpoint_dir=cache)
            self.assertEqual(sessions[0].run.call_count, 1)
            self.np.testing.assert_array_equal(resumed, self.separate(x))
            factory = Mock(side_effect=AssertionError("cached inference repeated"))
            cached = self.np.concatenate(list(sep._separated_blocks(lambda start, count: x[:, start:start + count],
                x.shape[1], factory, batch_splits=1, checkpoint_dir=cache)), axis=1)
            self.np.testing.assert_array_equal(cached, resumed)
            factory.assert_not_called()
            first = sorted(cache.glob("*.npz"))[0]
            first.write_bytes(b"corrupt")
            sessions = self.sessions()
            self.np.testing.assert_array_equal(self.separate(x, sessions=sessions, checkpoint_dir=cache), resumed)
            self.assertEqual(sessions[0].run.call_count, 1)

    def test_nonfinite_estimates_and_waveforms_are_rejected(self):
        np = self.np
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED"):
                sep._accompaniment_mask(np.zeros((2, 1, 1024)), np.full((2, 1, 1024), value))
            with self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED"):
                sep._finite(np.array([value]))

    def write_wav(self, path, values, *, rate=16000, channels=2):
        with wave.open(str(path), "wb") as writer:
            writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            writer.writeframes(self.np.asarray(values, dtype="<i2").tobytes())

    def test_gate_checks_converted_peak_duration_and_format(self):
        np = self.np
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.wav", Path(directory) / "output.wav"
            self.write_wav(source, np.full((16000, 2), 4000))
            for peak in (32767, -32768):
                self.write_wav(output, np.full((16000, 2), peak))
                with self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED.*clips"):
                    sep._validate_background(source, output)
            for frames in (15000, 17000):
                self.write_wav(output, np.zeros((frames, 2)))
                with self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED.*duration"):
                    sep._validate_background(source, output)
            self.write_wav(output, np.zeros((16000, 1)), channels=1)
            with self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED"):
                sep._validate_background(source, output)
            self.write_wav(output, np.full((16000, 2), 2000))
            metrics = sep._validate_background(source, output)
            self.assertAlmostEqual(metrics["background_rms_db"], metrics["removed_db"])
            self.assertAlmostEqual(metrics["source_rms_db"] - metrics["background_rms_db"], 6.0206, places=4)
            self.assertEqual(metrics["peak"], 2000 / 32768)

    @unittest.skipUnless(FFMPEG, "set DUBFLOW_TEST_FFMPEG for offline PCM conversion tests")
    def test_ffmpeg_preserves_rate_channels_duration_and_atomic_fallback(self):
        np = self.np
        with TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            models.mkdir()
            for stem in ("vocals", "accompaniment"):
                (models / f"spleeter-2stems-{stem}.onnx").write_bytes(b"fake model")
            for rate, channels in ((16000, 1), (32000, 2), (48000, 2)):
                source, output = root / f"source-{rate}.wav", root / f"background-{rate}.wav"
                values = np.tile((3000 * np.sin(2 * np.pi * 220 * np.arange(rate + 31) / rate))[:, None], (1, channels))
                self.write_wav(source, values, rate=rate, channels=channels)
                with patch.object(sep, "_create_session", side_effect=lambda _: self.sessions()[0]):
                    metrics = sep.separate_background(source, output, model_dir=models, ffmpeg_path=Path(FFMPEG))
                with wave.open(str(output), "rb") as reader:
                    self.assertEqual((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()), (rate, channels, 2))
                    self.assertLessEqual(abs(reader.getnframes() - len(values)), 1)
                self.assertEqual(metrics["backend"], sep.BACKEND)
                self.assertLess(metrics["peak"], .999)
                previous = output.read_bytes()
                # Native/encode failures never replace a previously valid bed.
                with patch.object(sep, "_separated_blocks", side_effect=RuntimeError("failed inference")), \
                     self.assertRaisesRegex(SeparationError, "SEPARATION_FAILED"):
                    sep.separate_background(source, output, model_dir=models, ffmpeg_path=Path(FFMPEG))
                self.assertEqual(output.read_bytes(), previous)
                self.assertFalse(list(root.glob(".background-*.wav")))
                if rate == 16000:
                    for invalid in (np.full((2, 44100), np.nan, np.float32),
                                    np.ones((2, 44100), np.float32), np.zeros((2, 22050), np.float32)):
                        with patch.object(sep, "_separated_blocks", return_value=iter((invalid,))), \
                             self.assertRaisesRegex(SeparationError, "SEPARATION_REJECTED"):
                            sep.separate_background(source, output, model_dir=models, ffmpeg_path=Path(FFMPEG))
                        self.assertEqual(output.read_bytes(), previous)

    @unittest.skipUnless(os.environ.get("DUBFLOW_TEST_SPLEETER_DIR") and FFMPEG,
                         "real model smoke requires DUBFLOW_TEST_SPLEETER_DIR and DUBFLOW_TEST_FFMPEG")
    def test_real_models_synthetic_music_and_noise_voice_pass_the_gate(self):
        np = self.np
        with TemporaryDirectory() as directory:
            root = Path(directory)
            models = root / "models"
            models.mkdir()
            # Research exports have short names; production artifacts stay pinned.
            for stem in ("vocals", "accompaniment"):
                name = f"spleeter-2stems-{stem}.onnx"
                original = Path(os.environ["DUBFLOW_TEST_SPLEETER_DIR"]) / name
                if not original.exists():
                    original = original.with_name(f"{stem}.onnx")
                shutil.copyfile(original, models / name)
            rate = 48000
            t = np.arange(rate * 3) / rate
            music = .08 * np.sin(2 * np.pi * 220 * t) + .04 * np.sin(2 * np.pi * 440 * t)
            voice = np.random.default_rng(171).normal(0, .05, len(t)) * ((t % 1) < .35)
            stereo = np.stack((music + voice, music * .9 + voice), axis=1)
            source, output = root / "source.wav", root / "background.wav"
            self.write_wav(source, stereo * 32768, rate=rate)
            result = sep.separate_background(source, output, model_dir=models, ffmpeg_path=Path(FFMPEG))
            self.assertEqual(result["backend"], sep.BACKEND)
            self.assertLessEqual(result["peak"], .999)
            self.assertTrue(all(np.isfinite(value) for value in result.values() if isinstance(value, float)))
            print("Real Spleeter synthetic smoke:", json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    unittest.main()

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
import wave

from engine.dubflow.models.runtime import load_profile
from engine.dubflow.separation import mdx, spleeter
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
from engine.dubflow.separation.mdx import separate_background
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_pins_the_unchanged_upstream_model(self):
        path = ROOT / "models/manifests/production-separation-mdx-v1.json"
        profile, artifacts = load_profile(path)
        self.assertEqual(profile, "dubflow-uvr-mdx-kim-vocal-2-v1")
        self.assertEqual([(item.relative_path.rsplit("/", 1)[1], item.size_bytes) for item in artifacts],
                         [(mdx.MODEL_FILE, 66759214)])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["distribution"], "download-on-opt-in")
        self.assertTrue(all(item.relative_path.startswith(data["model_dir"] + "/") for item in artifacts))
        self.assertIn("MIT License", (ROOT / "docs/licenses/uvr-mdx.md").read_text(encoding="utf-8"))

    def test_limiter_does_not_delay_the_accompaniment(self):
        # The editor subtracts the accompaniment from the source sample by sample.
        self.assertIn("latency=1", spleeter.LIMITER)


class _Identity:
    """A 'model' that returns its input: every sample is voice."""

    def __init__(self, scale=1.0):
        self.scale, self.calls = scale, 0

    def get_inputs(self):
        return [type("Input", (), {"name": "input"})()]

    def run(self, _outputs, feeds):
        self.calls += 1
        return [feeds["input"] * self.scale]


@unittest.skipUnless(HAS_NUMPY, "numpy is optional in deterministic CI")
class MdxMathTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np

    def signal(self, length):
        rng = self.np.random.default_rng(42)
        # Band-limited below DIM_F's 17.6 kHz so the cut bins hold no energy.
        white = rng.normal(0, .1, (2, length + 64)).astype(self.np.float32)
        kernel = self.np.hanning(9).astype(self.np.float32)
        kernel /= kernel.sum()
        return self.np.stack([self.np.convolve(channel, kernel, "same") for channel in white])[:, 32:32 + length]

    def test_stft_round_trip_and_model_layout(self):
        np = self.np
        chunk = self.signal(mdx.CHUNK)[None]
        spec = mdx._stft(chunk)
        self.assertEqual(spec.shape, (1, 4, mdx.DIM_F, mdx.DIM_T))
        self.assertEqual(spec.dtype, np.float32)
        back = mdx._istft(spec)
        self.assertEqual(back.shape, chunk.shape)
        error = np.sqrt(np.mean((back - chunk) ** 2)) / np.sqrt(np.mean(chunk ** 2))
        self.assertLess(error, 1e-2)

    def blocks(self, samples, session, **options):
        read = lambda first, count: samples[:, first:first + count]
        return list(mdx._voice_blocks(read, samples.shape[1], lambda: session, **options))

    def test_blocks_cover_the_file_in_order_without_seams(self):
        np = self.np
        for length, batch in ((1000, 1), (mdx.CHUNK * 3 + 777, 1), (mdx.CHUNK * 3 + 777, 2)):
            with self.subTest(length=length, batch=batch):
                samples = self.signal(length)
                fractions = []
                blocks = self.blocks(samples, _Identity(), batch=batch, progress=fractions.append)
                mix = np.concatenate([block[0] for block in blocks], axis=1)
                voice = np.concatenate([block[1] for block in blocks], axis=1)
                self.assertEqual(mix.shape, samples.shape)
                np.testing.assert_array_equal(mix, samples)
                # An all-voice model removes everything, seams included.
                residual = (mix - voice / mdx.COMPENSATE)
                self.assertLess(np.max(np.abs(residual)), 2e-2)
                self.assertEqual(fractions[-1], 1.0)
                self.assertEqual(fractions, sorted(fractions))

    def test_silent_model_keeps_the_source_untouched(self):
        np = self.np
        samples = self.signal(mdx.CHUNK + 5)
        blocks = self.blocks(samples, _Identity(0.0))
        self.assertEqual(float(np.max(np.abs(np.concatenate([block[1] for block in blocks], axis=1)))), 0.0)

    def test_non_finite_model_output_is_rejected(self):
        samples = self.signal(4000)
        with self.assertRaises(SeparationError) as caught:
            self.blocks(samples, _Identity(float("nan")))
        self.assertEqual(caught.exception.code, "SEPARATION_REJECTED")

    def test_cancel_stops_before_the_next_chunk(self):
        samples = self.signal(mdx.CHUNK * 4)
        session = _Identity()
        with self.assertRaises(SeparationError) as caught:
            self.blocks(samples, session, cancelled=lambda: session.calls >= 1)
        self.assertEqual(caught.exception.code, "SEPARATION_CANCELLED")
        self.assertEqual(session.calls, 1)


@unittest.skipUnless(HAS_NUMPY and FFMPEG, "needs numpy and ffmpeg")
class MdxEndToEndTests(unittest.TestCase):
    def test_background_is_aligned_gated_pcm(self):
        import numpy as np
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            source, background = Path(temp) / "source.wav", Path(temp) / "out" / "background.wav"
            rate = 48000
            t = np.arange(rate * 2) / rate
            tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            pcm = (np.stack([tone, tone], 1) * 32767).astype("<i2")
            with wave.open(str(source), "wb") as stream:
                stream.setnchannels(2), stream.setsampwidth(2), stream.setframerate(rate)
                stream.writeframes(pcm.tobytes())
            (Path(temp) / mdx.MODEL_FILE).write_bytes(b"model")
            with patch.object(mdx, "_create_session", return_value=_Identity(0.0)):
                metrics = mdx.separate_background(source, background, model_dir=Path(temp), ffmpeg_path=Path(FFMPEG))
            self.assertEqual(metrics["backend"], mdx.BACKEND)
            with wave.open(str(background), "rb") as stream:
                out = np.frombuffer(stream.readframes(stream.getnframes()), "<i2").reshape(-1, 2)
            self.assertEqual(out.shape, pcm.shape)
            # A silent voice leaves the accompaniment equal to the source, with no delay.
            core = slice(2000, -2000)
            self.assertLess(np.max(np.abs(out[core].astype(int) - pcm[core].astype(int))), 400)


if __name__ == "__main__":
    unittest.main()

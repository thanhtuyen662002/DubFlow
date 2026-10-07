from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts.adapter import TtsConfig, TtsInput, TtsRequest, TtsError
from engine.dubflow.tts.vieneu import load_vieneu_voice, VieNeuVietnameseTtsEngine, FILES
from engine.dubflow.tts.vieneu_native import NativeModel, VERSIONS, INFERENCE_RECIPE

ROOT = Path(__file__).resolve().parents[3]


class VieNeuPackTests(unittest.TestCase):
    def fixture(self, root):
        profile = json.loads((ROOT / "models/manifests/production-vieneu-v1.json").read_text(encoding="utf-8"))
        data = {f: b"fixture-data" for f in FILES}
        data["voices.json"] = json.dumps({"presets": {profile["voice_name"]: {}}}).encode()
        records = {k: {"sha256": hashlib.sha256(v).hexdigest(), "size_bytes": len(v)} for k, v in data.items()}
        tree = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        profile["model_tree_sha256"] = tree
        prefix = "tts/packs/vieneu-" + tree
        profile["artifacts"] = [{"id": "file-" + str(i), "path": prefix + "/" + k, "url": "https://example.invalid/" + k, **records[k]} for i, k in enumerate(sorted(data))]
        app, cache = root / "app", root / "models"
        app.mkdir()
        metadata, selected = app / "voice.json", app / "cpu.json"
        metadata.write_text(json.dumps(profile), encoding="utf-8")
        selected.write_text(json.dumps({"tts_neural_profile": "voice.json"}))
        installed = cache / prefix
        for k, payload in data.items():
            p = installed / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(payload)
        return app, cache, metadata, selected, installed, profile

    def test_warmed_pack_does_not_download_and_voice_change_changes_identity(self):
        with TemporaryDirectory() as directory:
            app, cache, metadata, selected, _, profile = self.fixture(Path(directory))
            with patch("engine.dubflow.models.runtime.urllib.request.urlopen", side_effect=AssertionError("offline cache must not download")):
                pack, voice = load_vieneu_voice(app, cache, selected)
            self.assertEqual(pack.sample_rate, 48000)
            self.assertEqual(pack.voice_name, "Ngọc Huyền")
            self.assertEqual(voice.license_id, "Apache-2.0")
            profile["voice_version"] = "1.0.1"
            metadata.write_text(json.dumps(profile), encoding="utf-8")
            _, changed = load_vieneu_voice(app, cache, selected)
            self.assertNotEqual(voice.content_hash(), changed.content_hash())

    def test_unsafe_manifest_rejected_before_download_or_writing_pack(self):
        for kind in ("traversal", "foreign", "duplicate", "hash", "recipe", "runtime", "license"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                app, cache, metadata, selected, installed, profile = self.fixture(Path(directory))
                original = (installed / "tts/config.json").read_bytes()
                if kind == "traversal": profile["artifacts"][0]["path"] = "../escape"
                if kind == "foreign": profile["artifacts"][0]["path"] += ".py"
                if kind == "duplicate": profile["artifacts"][1] = deepcopy(profile["artifacts"][0])
                if kind == "hash": profile["model_tree_sha256"] = "a" * 64
                if kind == "recipe": profile["inference"]["babble_retries"] = 9
                if kind == "runtime": profile["runtime_versions"]["vieneu"] = "latest"
                if kind == "license": profile["approved"] = False
                metadata.write_text(json.dumps(profile), encoding="utf-8")
                with patch("engine.dubflow.tts.vieneu.ensure_model_profile", side_effect=AssertionError("invalid inventory must not bootstrap")), self.assertRaises(TtsError):
                    load_vieneu_voice(app, cache, selected)
                self.assertEqual((installed / "tts/config.json").read_bytes(), original)
                self.assertFalse((Path(directory) / "escape").exists())

    def test_foreign_code_and_linked_model_data_are_rejected(self):
        for kind in ("foreign_code", "symlink"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                app, cache, _, selected, installed, _ = self.fixture(Path(directory))
                if kind == "foreign_code":
                    (installed / "modeling.py").write_text("raise RuntimeError('do not execute')")
                else:
                    target = installed / "tts/config.json"
                    target.unlink()
                    outside = Path(directory) / "outside"
                    outside.write_text("foreign")
                    try: target.symlink_to(outside)
                    except OSError: continue  # OS privilege not supplied in this lane.
                with self.assertRaisesRegex(TtsError, "VOICE_PATH_UNSAFE"):
                    load_vieneu_voice(app, cache, selected)

    def test_tamper_cannot_be_accepted_when_downloader_claims_ready(self):
        with TemporaryDirectory() as directory:
            app, cache, _, selected, installed, _ = self.fixture(Path(directory))
            (installed / "tts/config.json").write_bytes(b"changed-data")
            with patch("engine.dubflow.tts.vieneu.ensure_model_profile", return_value={"ready": True}), self.assertRaisesRegex(TtsError, "VOICE_PACK_CHECKSUM_MISMATCH"):
                load_vieneu_voice(app, cache, selected)


class VieNeuBoundsTests(unittest.TestCase):
    def test_no_eos_is_rejected_without_waveform_publication_or_hidden_retry(self):
        model = NativeModel.__new__(NativeModel)
        model.last_text = None
        model.last_samples = None
        model.np = SimpleNamespace(random=SimpleNamespace(seed=lambda _: None))
        model.phonemize = lambda _: "phones"
        model.codes, model.speaker = None, None
        calls = []
        model.engine = SimpleNamespace(ended=False, tokenizer=SimpleNamespace(encode=lambda _: SimpleNamespace(ids=[1])), infer=lambda **kw: calls.append(kw) or [0.1])
        with self.assertRaisesRegex(ValueError, "before end-of-speech"):
            model.generate({"text": "Xin chào", "speed": 1.0, "sequence": 1})
        self.assertEqual(len(calls), 1)
        self.assertIsNone(model.last_samples)

    def test_invalid_text_and_speed_fail_before_sdk_inference(self):
        model = NativeModel.__new__(NativeModel)
        for text, speed in (("", 1.), ("x" * 513, 1.), ("xin chào", float("nan")), ("xin chào", 1.31), ("xin chào", True)):
            with self.subTest(text=text[:20], speed=speed), self.assertRaises(ValueError):
                model.generate({"text": text, "speed": speed, "sequence": 1})


@unittest.skipUnless(os.environ.get("DUBFLOW_REAL_VIENEU_MODEL_ROOT"), "real pinned VieNeu data not supplied")
class ActualVieNeuTests(unittest.TestCase):
    def test_native_offline_speech_and_bounded_pitch_preserving_fit(self):
        pack, voice = load_vieneu_voice(ROOT, os.environ["DUBFLOW_REAL_VIENEU_MODEL_ROOT"], ROOT / "models/manifests/production-cpu-v1.json")
        engine = VieNeuVietnameseTtsEngine(pack, ffmpeg_path=os.environ["DUBFLOW_REAL_FFMPEG"])
        try:
            health = engine.healthcheck(voice)
            self.assertTrue(health.ready, health.condition)
            natural = engine._tts.generate("Xin chào Việt Nam.", 0, 1.)
            fitted = engine._tts.generate("Xin chào Việt Nam.", 0, 1.2)
            self.assertEqual(natural.sample_rate, 48000)
            self.assertGreater(max(abs(x) for x in natural.samples), 0.01)
            self.assertLess(len(fitted.samples), len(natural.samples))
            self.assertLess(abs(len(fitted.samples) - len(natural.samples) / 1.2), 4800)
            target = len(natural.samples) * 10 // 11
            segment = TtsInput("fit", "fit", "Xin chào Việt Nam.", TimePoint(0, TimeBase(1, 48000)), TimePoint(target, TimeBase(1, 48000)))
            result = engine.synthesize(TtsRequest("fit", segment, voice, TtsConfig(sample_rate=48000), "sha256:" + "a" * 64, 0, target))
            with TemporaryDirectory() as directory:
                path = Path(directory) / "fit.wav"
                path.write_bytes(result.audio_bytes)
                with wave.open(str(path)) as f:
                    self.assertEqual(f.getnframes(), target)
                    self.assertEqual(f.getframerate(), 48000)
            self.assertEqual(result.fit_mode, "speed_adjusted")
        finally:
            engine.close()

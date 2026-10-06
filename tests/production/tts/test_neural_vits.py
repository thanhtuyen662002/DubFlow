from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts.adapter import TtsConfig, TtsInput, TtsRequest, VoiceProfile
from engine.dubflow.tts.neural_vits import (
    ENGINE_ID, RUNTIME_VERSION, NeuralVoicePack, NeuralVietnameseTtsEngine,
    TtsError, _tree_valid, _unpack_archive, load_neural_voice,
)

ROOT = Path(__file__).resolve().parents[3]
BASE = TimeBase(1, 1000)


def tree_hash(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def archive(path: Path, entries: list[tuple[str, bytes | None]]) -> dict:
    records = {}
    with tarfile.open(path, "w:bz2") as writer:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            if data is None:
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                writer.addfile(member)
            else:
                member.size = len(data)
                writer.addfile(member, io.BytesIO(data))
                records[name.split("/", 1)[-1]] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    return records


class NeuralArchiveTests(unittest.TestCase):
    def test_pinned_tree_installs_and_conversion_scripts_are_not_installed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            records = archive(root / "voice.tar.bz2", [("pack/tokens.txt", b"a 1\n"), ("pack/vits-mimic3.py", b"raise RuntimeError('never execute')")])
            del records["vits-mimic3.py"]
            destination = root / "installed"
            _unpack_archive(root / "voice.tar.bz2", destination, "pack", tree_hash(records))
            self.assertTrue(_tree_valid(destination, tree_hash(records)))
            self.assertFalse((destination / "vits-mimic3.py").exists())

    def test_traversal_links_devices_and_duplicate_members_are_rejected(self) -> None:
        cases = [
            [("pack/../escape", b"x")],
            [("outside/tokens.txt", b"x")],
            [("pack/tokens.txt", None)],
            [("pack/CON", b"x")],
            [("pack/tokens.txt", b"x"), ("pack/tokens.txt", b"y")],
        ]
        for entries in cases:
            with self.subTest(entries=entries), TemporaryDirectory() as directory:
                root = Path(directory)
                archive(root / "voice.tar.bz2", entries)
                with self.assertRaises(TtsError):
                    _unpack_archive(root / "voice.tar.bz2", root / "installed", "pack", "a" * 64)
                self.assertFalse((root / "escape").exists())
                self.assertFalse((root / "installed").exists())

    def test_wrong_digest_preserves_existing_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "installed"
            destination.mkdir()
            (destination / "prior-good-artifact").write_bytes(b"retain")
            archive(root / "voice.tar.bz2", [("pack/tokens.txt", b"new")])
            with self.assertRaisesRegex(TtsError, "VOICE_PACK_CHECKSUM_MISMATCH"):
                _unpack_archive(root / "voice.tar.bz2", destination, "pack", "a" * 64)
            self.assertEqual((destination / "prior-good-artifact").read_bytes(), b"retain")

    def test_size_and_member_budgets_are_enforced(self) -> None:
        for field, limit in (("MAX_EXPANDED_BYTES", 2), ("MAX_FILES", 1)):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                root = Path(directory)
                records = archive(root / "voice.tar.bz2", [("pack/tokens.txt", b"data"), ("pack/README.md", b"text")])
                with patch("engine.dubflow.tts.neural_vits." + field, limit), self.assertRaises(TtsError):
                    _unpack_archive(root / "voice.tar.bz2", root / "installed", "pack", tree_hash(records))

    def test_cache_cannot_be_reauthenticated_with_a_forged_marker(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            records = archive(root / "voice.tar.bz2", [("pack/tokens.txt", b"original")])
            digest = tree_hash(records)
            destination = root / "installed"
            _unpack_archive(root / "voice.tar.bz2", destination, "pack", digest)
            (destination / "tokens.txt").write_bytes(b"tampered")
            forged = {"tokens.txt": {"sha256": hashlib.sha256(b"tampered").hexdigest(), "size_bytes": 8}}
            (destination / ".installed.json").write_text(json.dumps({"files": forged}))
            self.assertFalse(_tree_valid(destination, digest))


class ScriptedNative:
    """Native API seam used solely for duration and waveform error tests."""
    def __init__(self, frame_counts: list[int], value: float = 0.1) -> None:
        self.frame_counts = list(frame_counts)
        self.value = value
        self.speeds = []

    def generate(self, text, sid, speed):
        self.speeds.append(speed)
        return SimpleNamespace(samples=[self.value] * self.frame_counts.pop(0), sample_rate=22050)


class NeuralDurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pack = NeuralVoicePack(Path("unused"), 22050, 1, "sha256:" + "a" * 64, "sha256:" + "b" * 64, "CC-BY-4.0", "vi-test", "1", "model-test", "1")
        self.voice = VoiceProfile(voice_id="vi-test", voice_version="1", model_hash=self.pack.model_hash)
        self.config = TtsConfig(sample_rate=22050, requested_profile="cpu", max_attempts=1)
        segment = TtsInput("cue-1", "cue-1", "Xin chào", TimePoint(0, BASE), TimePoint(1000, BASE))
        self.request = TtsRequest("test", segment, self.voice, self.config, "sha256:" + "c" * 64, 0, 22050)

    def synthesize(self, native):
        engine = NeuralVietnameseTtsEngine(self.pack)
        engine._tts = native
        with patch("engine.dubflow.tts.neural_vits.importlib.metadata.version", return_value=RUNTIME_VERSION):
            return engine.synthesize(self.request)

    def test_short_natural_speech_is_padded_and_preserved(self) -> None:
        native = ScriptedNative([11025])
        result = self.synthesize(native)
        self.assertEqual(native.speeds, [1.0])
        self.assertEqual(result.fit_mode, "padded")
        with wave.open(io.BytesIO(result.audio_bytes)) as reader:
            self.assertEqual(reader.getnframes(), 22050)
            self.assertEqual(reader.getframerate(), 22050)
            self.assertEqual(reader.getnchannels(), 1)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertNotEqual(reader.readframes(11025), b"\0" * 22050)
            self.assertEqual(reader.readframes(11025), b"\0" * 22050)

    def test_safe_fit_generates_again_instead_of_cutting_speech(self) -> None:
        native = ScriptedNative([26460, 22050])
        result = self.synthesize(native)
        self.assertEqual(native.speeds, [1.0, 1.2])
        self.assertEqual(result.fit_mode, "speed_adjusted")
        self.assertEqual(result.speed_ratio_milli, 1200)

    def test_unsafe_fit_is_rejected_without_second_inference(self) -> None:
        native = ScriptedNative([44100])
        with self.assertRaisesRegex(TtsError, "DURATION_FIT_REQUIRED"):
            self.synthesize(native)
        self.assertEqual(native.speeds, [1.0])

    def test_failed_second_fit_never_cuts_spoken_samples(self) -> None:
        native = ScriptedNative([26460, 23000])
        with self.assertRaisesRegex(TtsError, "spoken samples will not be cut"):
            self.synthesize(native)

    def test_non_finite_native_audio_is_rejected(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(TtsError, "TTS_AUDIO_INVALID"):
                self.synthesize(ScriptedNative([100], value))

    def test_empty_audio_is_rejected(self) -> None:
        with self.assertRaisesRegex(TtsError, "TTS_AUDIO_INVALID"):
            self.synthesize(ScriptedNative([0]))

    def test_silent_native_audio_is_rejected(self) -> None:
        with self.assertRaisesRegex(TtsError, "TTS_AUDIO_INVALID"):
            self.synthesize(ScriptedNative([100], 0.0))

    def test_unpinned_runtime_is_not_accepted(self) -> None:
        with patch("engine.dubflow.tts.neural_vits.importlib.metadata.version", return_value="unrelated"):
            health = NeuralVietnameseTtsEngine(self.pack).healthcheck(self.voice)
        self.assertFalse(health.ready)
        self.assertEqual(health.code, "TTS_RUNTIME_VERSION_MISMATCH")


@unittest.skipUnless(os.environ.get("DUBFLOW_REAL_TTS_MODEL_ROOT"), "real-model qualification runs separately from hermetic PR checks")
class RealNeuralVoiceTests(unittest.TestCase):
    def test_pinned_native_vietnamese_model_produces_pcm(self) -> None:
        pack, voice = load_neural_voice(ROOT, os.environ["DUBFLOW_REAL_TTS_MODEL_ROOT"], ROOT / "models/manifests/production-cpu-v1.json")
        engine = NeuralVietnameseTtsEngine(pack)
        self.assertTrue(engine.healthcheck(voice).ready)
        self.assertEqual(engine.capabilities().engine_id, ENGINE_ID)
        config = TtsConfig(sample_rate=22050, requested_profile="cpu", max_attempts=1)
        segment = TtsInput("native-vi", "native-vi", "Xin chào Việt Nam.", TimePoint(0, BASE), TimePoint(6000, BASE))
        result = engine.synthesize(TtsRequest("real-vi", segment, voice, config, "sha256:" + "d" * 64, 0, 132300))
        with wave.open(io.BytesIO(result.audio_bytes)) as reader:
            pcm = reader.readframes(reader.getnframes())
            self.assertEqual(reader.getnframes(), 132300)
            self.assertEqual(reader.getframerate(), 22050)
            self.assertGreater(sum(abs(value) for value in pcm), 0)
        # This checks real backend execution and PCM, not intelligibility.
        # Back-ASR/human quality evidence is an additional qualification gate.


if __name__ == "__main__":
    unittest.main()

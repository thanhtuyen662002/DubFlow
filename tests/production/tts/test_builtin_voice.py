from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts import (
    BuiltinVietnameseTtsEngine,
    LocalTtsAdapter,
    TtsConfig,
    TtsInput,
    TtsProvenance,
    VoiceProfile,
    load_production_voice,
)


ROOT = Path(__file__).resolve().parents[3]
BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "b" * 64


class BuiltinVoiceTests(unittest.TestCase):
    def test_pack_is_app_owned_pinned_and_offline(self) -> None:
        pack, voice = load_production_voice(ROOT)
        self.assertEqual(pack.path, ROOT / "models" / "voices" / "vi-builtin-v1.json")
        self.assertTrue(pack.model_hash.startswith("sha256:"))
        self.assertEqual(voice.model_id, "dubflow-vi-builtin-v1")
        self.assertEqual(voice.license_id, "dubflow-builtin-voice-1.0")
        self.assertTrue(voice.approved)
        self.assertFalse(voice.network_required or voice.credential_required)
        self.assertEqual(BuiltinVietnameseTtsEngine(pack).capabilities().engine_id, "dubflow-vi-builtin-v1")

    def test_engine_emits_exact_duration_non_silent_waveform(self) -> None:
        pack, voice = load_production_voice(ROOT)
        config = TtsConfig(requested_profile="cpu")
        provenance = TtsProvenance(
            "test-production-tts",
            "1.0.0",
            "dubflow-vi-builtin-v1",
            "python-stdlib",
            "timeline-v1",
            config.content_hash(),
            INPUT_HASH,
            voice.model_id,
            voice.model_version,
            voice.model_hash,
            voice.content_hash(),
            voice.voice_id,
            voice.voice_version,
            "cpu",
            "cpu",
            config.resource,
        )
        segment = TtsInput(
            "cue-1",
            "cue-1",
            "Xin chào Việt Nam",
            TimePoint(250, BASE),
            TimePoint(1750, BASE),
            source_language="en",
        )
        with TemporaryDirectory(prefix="dubflow-production-voice-") as directory:
            document = LocalTtsAdapter(
                BuiltinVietnameseTtsEngine(pack),
                config=config,
                provenance=provenance,
                voice=voice,
                output_dir=directory,
            ).synthesize((segment,), input_hash=INPUT_HASH)
        artifact = document.artifacts[0]
        self.assertEqual(artifact.frame_count, 24_000)
        self.assertGreater(artifact.metrics.rms_milli, 0)
        self.assertEqual(document.provenance.backend_id, "dubflow-vi-builtin-v1")
        self.assertEqual(document.provenance.hardware_profile, "cpu")

    def test_manifest_hash_mismatch_is_rejected_before_synthesis(self) -> None:
        pack_path = ROOT / "models" / "voices" / "vi-builtin-v1.json"
        profile_path = ROOT / "models" / "manifests" / "production-cpu-v1.json"
        profile = profile_path.read_text(encoding="utf-8")
        with TemporaryDirectory(prefix="dubflow-production-manifest-") as directory:
            temp_root = Path(directory)
            (temp_root / "models" / "manifests").mkdir(parents=True)
            (temp_root / "models" / "voices").mkdir(parents=True)
            (temp_root / "models" / "voices" / pack_path.name).write_bytes(pack_path.read_bytes())
            (temp_root / "models" / "manifests" / profile_path.name).write_text(profile.replace("f2bd5d89", "0" * 8), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "VOICE_PACK_CHECKSUM_MISMATCH"):
                load_production_voice(temp_root)


if __name__ == "__main__":
    unittest.main()

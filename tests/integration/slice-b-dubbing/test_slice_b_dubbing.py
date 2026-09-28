from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "minimal-pipeline-wiring"))

from b1_pipeline import B1Pipeline, B1PipelineConfig  # noqa: E402
from engine.dubflow.tts import EngineCapabilities, EngineHealth, TtsBackendError  # noqa: E402


FIXTURES = ROOT / "tests" / "integration" / "local_file_slice" / "fixtures"


class AlwaysFailingTts:
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities("failing-fixture", sample_rates=(16000,), channels=(1,))

    def healthcheck(self, voice) -> EngineHealth:
        return EngineHealth(True)

    def synthesize(self, request):
        raise TtsBackendError("MODEL_UNAVAILABLE", "fixture TTS model intentionally unavailable", retryable=False)


class AlwaysFailingAsr:
    def transcribe(self, chunk):
        raise RuntimeError("ASR should have been satisfied by the durable checkpoint")


class SliceBDubbingIntegrationTests(unittest.TestCase):
    def test_portrait_and_landscape_produce_dubbed_mix_and_editable_audio_asset(self) -> None:
        cases = (("portrait.mp4", 360, 640, "portrait"), ("landscape.mp4", 640, 360, "landscape"))
        with tempfile.TemporaryDirectory(prefix="dubflow-slice-b-dubbing-") as directory:
            root = Path(directory)
            for fixture_name, width, height, aspect in cases:
                with self.subTest(aspect=aspect):
                    result = B1Pipeline(
                        FIXTURES / fixture_name,
                        root / f"{aspect}.mp4",
                        config=B1PipelineConfig(width, height, enable_dubbing=True),
                    ).run()
                    self.assertEqual(result.report["orientation"], aspect)
                    self.assertEqual(result.export.audio_mode, "dubbed")
                    self.assertIsNotNone(result.tts)
                    self.assertIsNotNone(result.mix)
                    self.assertEqual(len(result.tts.artifacts), 2)
                    self.assertEqual(result.report["audio"]["policy"], "safe_duck_mix")
                    self.assertFalse(result.report["dubbing"]["fallback_to_b1"])
                    self.assertTrue(Path(result.mix.final_mix.path).is_file())
                    self.assertEqual(
                        [item["kind"] for item in json_assets(result.export.editable_pack["path"])],
                        ["subtitle", "dub_audio"],
                    )

    def test_tts_failure_falls_back_to_valid_b1_vietsub_without_stopping_next_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-slice-b-fallback-") as directory:
            root = Path(directory)
            failed = B1Pipeline(
                FIXTURES / "landscape.mp4",
                root / "failed-tss.mp4",
                config=B1PipelineConfig(640, 360, enable_dubbing=True),
                tts_engine=AlwaysFailingTts(),
            ).run()
            self.assertEqual(failed.export.audio_mode, "original")
            self.assertTrue(failed.report["dubbing"]["fallback_to_b1"])
            self.assertIn("TTS_OR_AUDIO_MIX_FALLBACK_TO_B1_VIETSUB", failed.report["capability_downgrades"])
            self.assertTrue(failed.report["dubbing"]["tts_failures"])
            successful = B1Pipeline(
                FIXTURES / "portrait.mp4",
                root / "successful.mp4",
                config=B1PipelineConfig(360, 640, enable_dubbing=True),
            ).run()
            self.assertEqual(successful.export.audio_mode, "dubbed")
            self.assertTrue(Path(successful.export.output["path"]).is_file())

    def test_checkpoint_reuses_upstream_analysis_before_dubbing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-slice-b-restart-") as directory:
            root = Path(directory)
            checkpoint = root / "analysis.checkpoint.json"
            config = B1PipelineConfig(640, 360, enable_dubbing=True)
            first = B1Pipeline(FIXTURES / "landscape.mp4", root / "first.mp4", config=config, checkpoint_path=checkpoint).run()
            resumed_pipeline = B1Pipeline(
                FIXTURES / "landscape.mp4",
                root / "resumed.mp4",
                config=config,
                checkpoint_path=checkpoint,
                asr_backend=AlwaysFailingAsr(),
            )
            resumed = resumed_pipeline.run()
            self.assertTrue(first.checkpoint_path.is_file())
            self.assertEqual(resumed_pipeline.analysis_calls, ())
            self.assertGreater(len(resumed.checkpoint_reused_chunks), 0)
            self.assertEqual(resumed.export.audio_mode, "dubbed")

    def test_no_source_audio_keeps_safe_export_mode_and_downgrade_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubflow-slice-b-no-audio-") as directory:
            result = B1Pipeline(
                FIXTURES / "portrait.mp4",
                Path(directory) / "no-audio.mp4",
                config=B1PipelineConfig(360, 640, source_has_audio=False, enable_dubbing=True),
            ).run()
        self.assertEqual(result.export.audio_mode, "none")
        self.assertTrue(result.report["dubbing"]["fallback_to_b1"])
        self.assertIn("TTS_OR_AUDIO_MIX_FALLBACK_TO_B1_VIETSUB", result.report["capability_downgrades"])


def json_assets(path: str) -> list[dict]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))["assets"]


if __name__ == "__main__":
    unittest.main()

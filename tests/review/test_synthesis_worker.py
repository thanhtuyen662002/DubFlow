from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from engine.dubflow.review import ReviewError
from engine.dubflow.review.synthesis_input import prepare_cue_synthesis
from engine.dubflow.review.synthesis_worker import (_writer, load_production_cue_engine,
                                                  synthesize_cue_candidate)
from engine.dubflow.tts import EngineSynthesis, ResourceProfile, TtsConfig, TtsStageError
from engine.dubflow.tts import vieneu
from engine.dubflow.worker.b2_audio import B2_TTS_PRODUCER_VERSION
import test_synthesis_input as inputs


class TrackingEngine(inputs.helpers.DeterministicFixtureEngine):
    def __init__(self):
        super().__init__(); self.closed = 0; self.segments = []

    def synthesize(self, request):
        self.segments.append(request.segment)
        return super().synthesize(request)

    def close(self):
        self.closed += 1


class CueSynthesisWorkerTests(unittest.TestCase):
    def request(self, directory):
        before, after, args = inputs.CueSynthesisInputTests().correction(directory)
        return prepare_cue_synthesis(before, after, **args)

    def run_candidate(self, request, root, engine, **kwargs):
        return synthesize_cue_candidate(request.encoded, expected_hash=request.input_hash,
                output_root=root, engine_factory=lambda admitted: engine,
                recipe_identity="fixture-source-recipe-v1", **kwargs)

    def test_one_cue_then_validated_resume_skips_inference_and_closes_engine(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-") as directory:
            request = self.request(directory); root = Path(directory) / "private output"
            engine = TrackingEngine(); result = self.run_candidate(request, root, engine)
            self.assertEqual([item.segment_id for item in engine.segments], ["cue-a"])
            self.assertEqual(engine.segments[0].normalized_text, "Ngồi yên.")
            self.assertEqual(engine.closed, 1)
            original_audio = Path(result.document.artifacts[0].path).read_bytes()
            original_document = result.document_path.read_bytes()
            original_spoken = result.spoken_text_path.read_bytes()
            speech = json.loads(result.spoken_text_path.read_bytes())
            self.assertEqual(speech["entries"][0]["spoken_text"], "Ngồi yên.")
            resumed = self.run_candidate(request, root, engine)
            self.assertEqual(len(engine.segments), 1)
            self.assertEqual(engine.closed, 2)
            self.assertEqual(Path(resumed.document.artifacts[0].path).read_bytes(), original_audio)
            self.assertEqual(resumed.document.provenance, request.provenance)
            self.assertEqual(resumed.document_path.read_bytes(), original_document)
            self.assertEqual(resumed.spoken_text_path.read_bytes(), original_spoken)
            observation = json.loads((resumed.document_path.parent / "execution_observation_v1.json").read_bytes())
            self.assertEqual(observation["chunks"][0]["status"], "skipped")
            self.assertTrue(any("reused TTS checkpoint" in warning for warning in observation["warnings"]))

    def test_corrupt_checkpoint_wave_rebuilds_only_that_cue(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-corrupt-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            engine = TrackingEngine(); result = self.run_candidate(request, root, engine)
            audio = Path(result.document.artifacts[0].path); original = audio.read_bytes()
            audio.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            self.run_candidate(request, root, engine)
            self.assertEqual(len(engine.segments), 2)
            self.assertEqual(audio.read_bytes(), original)

    def test_missing_spoken_receipt_rebuilds_instead_of_claiming_valid_reuse(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-spoken-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            engine = TrackingEngine(); result = self.run_candidate(request, root, engine)
            receipt = next((result.document_path.parent / "tts" / "spoken_text").glob("*.json"))
            receipt.unlink()
            result = self.run_candidate(request, root, engine)
            self.assertEqual(len(engine.segments), 2)
            self.assertTrue(any("SPOKEN_TEXT_CHECKPOINT_INVALID" in item for item in result.document.warnings))

    def test_bad_input_hash_and_existing_foreign_request_refuse_without_inference(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-pin-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            engine = TrackingEngine()
            with self.assertRaises(ReviewError):
                synthesize_cue_candidate(request.encoded, expected_hash=inputs.PIN,
                    output_root=root, engine_factory=lambda _: engine, recipe_identity="fixture-v1")
            self.assertFalse(root.exists())
            generation = root / ("cue-" + request.input_hash[7:]); generation.mkdir(parents=True)
            (generation / "input.json").write_bytes(b"foreign")
            with self.assertRaisesRegex(ReviewError, "another request"):
                self.run_candidate(request, root, engine)
            self.assertEqual(engine.segments, [])
            self.assertEqual((generation / "input.json").read_bytes(), b"foreign")

    def test_invalid_wave_never_publishes_success_document_and_engine_closes(self):
        class InvalidEngine(TrackingEngine):
            def synthesize(self, request):
                return EngineSynthesis(b"invalid", sample_rate=16000, channels=1, bits_per_sample=16)
        with TemporaryDirectory(prefix="dubflow-review-candidate-invalid-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            engine = InvalidEngine()
            with self.assertRaises(TtsStageError): self.run_candidate(request, root, engine)
            self.assertEqual(engine.closed, 1)
            self.assertEqual(list(root.rglob("tts_document.json")), [])

    def test_interrupted_document_write_preserves_checkpoint_for_resume(self):
        from engine.dubflow.review import synthesis_worker as worker
        with TemporaryDirectory(prefix="dubflow-review-candidate-interrupted-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            engine = TrackingEngine(); atomic = worker._atomic
            def fail_document(path, raw):
                if path.name == "tts_document.json": raise OSError("simulated disk loss")
                return atomic(path, raw)
            with patch.object(worker, "_atomic", side_effect=fail_document):
                with self.assertRaises(OSError): self.run_candidate(request, root, engine)
            self.assertEqual(list(root.rglob("tts_document.json")), [])
            self.assertEqual(len(engine.segments), 1)
            result = self.run_candidate(request, root, engine)
            self.assertEqual(len(engine.segments), 1)
            self.assertTrue(result.document_path.is_file())

    def test_concurrent_generation_writer_refuses_and_releases_on_exit(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-busy-") as directory:
            request = self.request(directory); root = Path(directory) / "private"
            generation = root / ("cue-" + request.input_hash[7:]); generation.mkdir(parents=True)
            engine = TrackingEngine()
            with _writer(generation):
                with self.assertRaisesRegex(ReviewError, "writer is busy"):
                    self.run_candidate(request, root, engine)
            self.assertEqual(engine.segments, [])
            self.run_candidate(request, root, engine)
            self.assertEqual(len(engine.segments), 1)

    def test_production_factory_rejects_fixture_producer_before_model_provisioning(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-factory-") as directory:
            request = self.request(directory)
            with patch("engine.dubflow.review.synthesis_worker.vieneu.load_vieneu_voice") as load:
                with self.assertRaisesRegex(ReviewError, "producer differs"):
                    load_production_cue_engine(request, app_root=Path(directory), model_root=Path(directory),
                                               profile_path=Path(directory)/"profile.json", ffmpeg_path=Path(directory)/"ffmpeg.exe")
                load.assert_not_called()

    def test_production_factory_rejects_config_and_voice_changes_before_inference(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-profile-") as directory:
            request = self.request(directory)
            config = TtsConfig(sample_rate=48000, channels=1, requested_profile="cpu", max_attempts=1,
                    max_text_chars=512, max_duration_error_ticks=80, min_speed_ratio_milli=800,
                    max_speed_ratio_milli=1300,
                    resource=ResourceProfile(max_threads=2, max_memory_mb=2048, max_batch_items=1))
            provenance = replace(request.provenance, producer="dubflow-production-tts",
                    producer_version=B2_TTS_PRODUCER_VERSION, backend_id=vieneu.ENGINE_ID,
                    runtime=vieneu.RUNTIME_ID, hardware_profile="cpu", requested_profile="cpu",
                    config_hash=config.content_hash(), resource=config.resource)
            admitted = replace(request, provenance=provenance, config=config)
            ffmpeg = Path(directory) / "ffmpeg.exe"; ffmpeg.write_bytes(b"fixture, never executed")
            args = dict(app_root=Path(directory), model_root=Path(directory),
                        profile_path=Path(directory)/"profile.json", ffmpeg_path=ffmpeg)
            with patch("engine.dubflow.review.synthesis_worker.vieneu.load_vieneu_voice") as load:
                with self.assertRaisesRegex(ReviewError, "configuration differs"):
                    load_production_cue_engine(replace(admitted, config=replace(config, sample_rate=16000)), **args)
                load.assert_not_called()
                load.return_value = (object(), replace(request.voice, voice_version="another"))
                with patch("engine.dubflow.review.synthesis_worker.vieneu.VieNeuVietnameseTtsEngine") as engine:
                    with self.assertRaisesRegex(ReviewError, "voice/model profile differs"):
                        load_production_cue_engine(admitted, **args)
                    engine.assert_not_called()

    def test_owned_cli_bad_request_pin_returns_no_document_or_private_output(self):
        with TemporaryDirectory(prefix="dubflow-review-candidate-cli-") as directory:
            request = self.request(directory); path = Path(directory)/"input.json"; path.write_bytes(request.encoded)
            root = Path(directory)/"private"
            worker = Path(__file__).resolve().parents[2]/"engine/dubflow/review/synthesis_worker.py"
            command = [os.environ.get("DUBFLOW_TEST_PYTHON", sys.executable), "-I", "-S", "-B", str(worker),
                "--request", str(path), "--request-sha256", inputs.PIN,
                "--app-root", directory, "--model-root", directory,
                "--profile", str(Path(directory)/"missing-profile.json"),
                "--ffmpeg", str(Path(directory)/"missing.exe"), "--output-root", str(root)]
            result = subprocess.run(command, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            error = json.loads(result.stderr); self.assertEqual(error["code"], "REVIEW_CUE_SYNTHESIS_FAILED")
            self.assertIn("trusted pin", error["message"])
            self.assertFalse(root.exists())


if __name__ == "__main__": unittest.main()

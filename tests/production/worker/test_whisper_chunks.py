from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.asr import AdapterConfig, ChunkPlanner, TimeBase, TimeInterval, TimePoint
from engine.dubflow.worker import production_job as worker
from engine.dubflow.worker.whisper_chunks import CORE_SAMPLES, OVERLAP_SAMPLES, plan_chunks, reconcile
from engine.dubflow.worker.whisper_cues import SAMPLE_RATE, cue_from_words, segment_cues


def word(text, start, end, probability=.8):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def speech(text="Hello", words=None):
    return SimpleNamespace(text=text, start=20., end=21., words=words or [word(text, 20., 21.)])


class ChunkedAsrTests(unittest.TestCase):
    def test_balanced_plan_has_no_tiny_tail_and_bounds_whole_six_hour_input(self):
        for total in (1, CORE_SAMPLES, CORE_SAMPLES + 1, 6 * 3600 * SAMPLE_RATE):
            chunks = plan_chunks(total, "sha256:" + "a" * 64)
            self.assertEqual(chunks[0].core.start.ticks, 0)
            self.assertEqual(chunks[-1].core.end.ticks, total)
            for left, right in zip(chunks, chunks[1:]):
                self.assertEqual(left.core.end, right.core.start)
            self.assertTrue(all(chunk.window.end.ticks - chunk.window.start.ticks <= CORE_SAMPLES + 2 * OVERLAP_SAMPLES for chunk in chunks))
            if total > CORE_SAMPLES:
                self.assertGreater(chunks[-1].core.end.ticks - chunks[-1].core.start.ticks, CORE_SAMPLES // 2 - 1)

    def test_window_offsets_are_added_after_sdk_rounding(self):
        source = SimpleNamespace(text="A", start=1., end=2., words=[word("A", 1.00001, 1.20001)])
        cue, = segment_cues(source, 3 * SAMPLE_RATE, sample_offset=1_234_567)
        self.assertEqual(cue.evidence["start_sample"], 1_234_567 + 16_000)
        self.assertEqual(cue.evidence["end_sample"], 1_234_567 + 19_201)
        self.assertEqual(cue.evidence["words"][0]["start_sample"], cue.evidence["start_sample"])

    def source(self, root):
        (root / "asr/faster-whisper-small").mkdir(parents=True, exist_ok=True)
        path = root / "source.wav"
        if not path.exists():
            with wave.open(str(path), "wb") as writer:
                writer.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                for second in range(250):
                    writer.writeframes(int(second).to_bytes(2, "little", signed=True) * SAMPLE_RATE)
        return path

    def run_model(self, root, *, language="auto", on_chunk=None, binding="model-v1", empty_first=False, source=None):
        path = self.source(root)
        calls = []
        instances = []
        class Model:
            def __init__(self, *args, **kwargs):
                instances.append(kwargs)
            def transcribe(self, window_path, **kwargs):
                with wave.open(window_path, "rb") as reader:
                    frames = reader.getnframes()
                    first_sample = int.from_bytes(reader.readframes(1), "little", signed=True)
                calls.append({"frames": frames, "first_sample": first_sample, **kwargs})
                observed = "zh" if empty_first else "en"
                if empty_first and first_sample == 0:
                    return iter(()), SimpleNamespace(language="de", language_probability=.2)
                return iter([source or speech()]), SimpleNamespace(language=observed, language_probability=.9)
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
            result = worker._transcribe_with_faster_whisper(path, root, language, on_chunk=on_chunk, model_binding=binding)
        return result, calls, instances

    def run_gpu(self, root, *, fail_construct=False, fail_on_call=None, error=None):
        """A CUDA model that fails as asked; the CPU model is the normal fake."""
        path = self.source(root)
        devices, fallbacks = [], []
        class Model:
            def __init__(self, *args, **kwargs):
                devices.append((kwargs["device"], kwargs["compute_type"]))
                if kwargs["device"] == "cuda" and fail_construct:
                    raise RuntimeError("CUDA failed with error no kernel image is available")
                self.device, self.calls = kwargs["device"], 0
            def transcribe(self, window_path, **kwargs):
                self.calls += 1
                if self.device == "cuda" and self.calls == fail_on_call:
                    raise error or RuntimeError("CUDA failed with error out of memory")
                def segments():  # Faster-Whisper decodes lazily, while cues are read.
                    yield speech()
                return segments(), SimpleNamespace(language="en", language_probability=.9)
        device = SimpleNamespace(device="cuda", compute_type="int8_float16")
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
            result = worker._transcribe_with_faster_whisper(path, root, "auto", device=device, model_binding="model-v1",
                                                            on_fallback=lambda index, error: fallbacks.append((index, str(error))))
        return result, devices, fallbacks

    def test_gpu_chunks_match_cpu_and_resume_across_devices(self):
        with TemporaryDirectory() as cpu_dir, TemporaryDirectory() as gpu_dir:
            expected, _, _ = self.run_model(Path(cpu_dir))
            result, devices, fallbacks = self.run_gpu(Path(gpu_dir))
            self.assertEqual(devices, [("cuda", "int8_float16")])
            self.assertEqual((fallbacks, result.cues), ([], expected.cues))
            # Chunk identity ignores the device, so a CPU run reuses GPU chunks.
            replay, calls, instances = self.run_model(Path(gpu_dir))
            self.assertEqual((calls, instances, replay), ([], [], result))

    def test_cuda_construction_failure_runs_every_chunk_on_cpu(self):
        with TemporaryDirectory() as directory:
            expected, _, _ = self.run_model(Path(directory) / "cpu")
            result, devices, fallbacks = self.run_gpu(Path(directory) / "gpu", fail_construct=True)
            self.assertEqual(devices, [("cuda", "int8_float16"), ("cpu", "int8")])
            self.assertEqual([index for index, _ in fallbacks], [0])
            self.assertIn("no kernel image", fallbacks[0][1])
            self.assertEqual(result.cues, expected.cues)

    def test_out_of_memory_mid_job_retries_that_chunk_and_continues_on_cpu(self):
        with TemporaryDirectory() as directory:
            expected, _, _ = self.run_model(Path(directory) / "cpu")
            result, devices, fallbacks = self.run_gpu(Path(directory) / "gpu", fail_on_call=2)
            self.assertEqual(devices, [("cuda", "int8_float16"), ("cpu", "int8")])
            self.assertEqual([index for index, _ in fallbacks], [1])
            self.assertEqual(result.cues, expected.cues)
            self.assertEqual(len(list((Path(directory) / "gpu" / "asr-chunks").rglob("*.json"))), 3)

    def test_content_failures_are_not_retried_on_cpu(self):
        from engine.dubflow.worker.whisper_chunks import ChunkAsrError
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_UNRESOLVED"):
                self.run_gpu(Path(directory), fail_on_call=1, error=ChunkAsrError("SOURCE_LANGUAGE_UNRESOLVED", "no language"))
            self.assertFalse(list((Path(directory) / "asr-chunks").rglob("*.json")))

    def test_cpu_failure_after_fallback_still_fails_the_stage(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            class Broken:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError(kwargs["device"] + " unavailable")
            (root / "asr/faster-whisper-small").mkdir(parents=True)
            with wave.open(str(root / "source.wav"), "wb") as writer:
                writer.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                writer.writeframes(b"\x01\x00" * SAMPLE_RATE)
            fallbacks = []
            with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Broken)}), \
                 self.assertRaisesRegex(worker.ProductionJobError, "ASR_FAILED: cpu unavailable"):
                worker._transcribe_with_faster_whisper(root / "source.wav", root, "auto", device=SimpleNamespace(device="cuda", compute_type="float16"),
                                                       on_fallback=lambda index, error: fallbacks.append(index))
            self.assertEqual(fallbacks, [0])

    def test_device_choice_is_recorded_as_job_warnings(self):
        from engine.dubflow.accelerator import nvidia
        card = nvidia.NvidiaDevice("NVIDIA GeForce RTX 3060", 12 * 1024 ** 3, (8, 6), 12080)
        config = SimpleNamespace(app_root=Path("app"), model_root=Path("models"), accelerator="auto")
        cases = ((nvidia.AsrDevice("cuda", "int8_float16", card), ["ASR_ACCELERATOR: cuda int8_float16 on NVIDIA GeForce RTX 3060 (12 GB)"]),
                 (nvidia.AsrDevice("cpu", "int8", card, "compute capability 5.2 is below 6.1"), ["ACCELERATOR_FALLBACK: ASR ran on the CPU: compute capability 5.2 is below 6.1"]),
                 (nvidia.AsrDevice("cpu", "int8"), []))
        for device, expected in cases:
            with self.subTest(device=device.device, reason=device.reason):
                warnings = []
                with patch.object(nvidia, "select_asr_device", return_value=device) as select:
                    self.assertIs(worker._asr_device(config, SimpleNamespace(progress=lambda *a, **k: None), warnings), device)
                self.assertEqual(warnings, expected)
                self.assertEqual(select.call_args.args[2], "auto")

    def test_committed_chunk_survives_interruption_and_resume_skips_model_work(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def interrupt(done, total, chunk_id, digest):
                self.assertEqual((done, total), (1, 3))
                self.assertTrue(digest.startswith("sha256:"))
                raise RuntimeError("simulated interruption after atomic chunk commit")
            with self.assertRaisesRegex(worker.ProductionJobError, "ASR_FAILED"):
                self.run_model(root, on_chunk=interrupt)
            first, = (root / "asr-chunks").rglob("*.json")
            snapshot = (first.read_bytes(), first.stat().st_mtime_ns)
            result, calls, instances = self.run_model(root)
            self.assertEqual(len(calls), 2)
            self.assertEqual([call["language"] for call in calls], ["en", "en"])
            self.assertEqual((first.read_bytes(), first.stat().st_mtime_ns), snapshot)
            self.assertTrue(all(call["frames"] <= CORE_SAMPLES + 2 * OVERLAP_SAMPLES for call in calls))
            self.assertEqual([call["first_sample"] for call in calls], [73, 156])
            replay, calls, instances = self.run_model(root)
            self.assertEqual((calls, instances), ([], []))
            self.assertEqual(result, replay)

    def test_corrupt_middle_chunk_regenerates_only_that_chunk(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _, _ = self.run_model(root)
            paths = list((root / "asr-chunks").rglob("*.json"))
            middle = next(path for path in paths if json.loads(path.read_text())["payload"]["chunk"]["index"] == 1)
            good = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths if path != middle}
            middle.write_bytes(b"{broken")
            actual, calls, _ = self.run_model(root)
            self.assertEqual(len(calls), 1)
            self.assertEqual(actual.cues, expected.cues)
            for path, snapshot in good.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)

    def test_recipe_model_binding_and_requested_language_do_not_relabel_old_chunks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("engine.dubflow.worker.whisper_cues.ASR_RECIPE", "obsolete-recipe"), patch("engine.dubflow.worker.whisper_chunks.ASR_RECIPE", "obsolete-recipe"):
                self.run_model(root)
            old = {path: path.read_bytes() for path in (root / "asr-chunks").rglob("*.json")}
            _, calls, _ = self.run_model(root)
            self.assertEqual(len(calls), 3)
            _, calls, _ = self.run_model(root, binding="model-v2")
            self.assertEqual(len(calls), 3)
            _, calls, _ = self.run_model(root, language="en")
            self.assertEqual(len(calls), 3)
            for path, content in old.items():
                self.assertEqual(path.read_bytes(), content)

    def test_drift_sentence_survives_reconciliation_checkpoint_and_chunk_resume(self):
        source = SimpleNamespace(text="Are you hurt?", start=27.43, end=47.47,
                                 words=[word("Are", 27.43, 27.97, .33), word(" you", 27.97, 33.19), word(" hurt?", 47.17, 47.47)])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result, calls, _ = self.run_model(root, source=source)
            self.assertEqual(len(result.cues), 3)
            for cue in result.cues:
                self.assertEqual((cue.source_text, cue.confidence), ("Are you hurt?", 0))
                self.assertIn(cue.end_ms - cue.start_ms, (5760, 5761))
                item = result.asr_evidence["cue_quality"][cue.cue_id]
                self.assertEqual(item["end_sample"] - item["start_sample"], 92160)
                self.assertEqual(item["review_reason"], "word-alignment-drift")
                self.assertEqual(len(item["alignment_drift"]["words"]), 3)
                self.assertEqual(len(item["chunk_origins"]), 1)
            document = {"schema_version": 4, "source": "faster-whisper", "requested_source_language": "auto",
                        **result.language_metadata(), "asr_evidence": result.asr_evidence}
            self.assertIsNotNone(worker._language_checkpoint(result.cues, json.loads(json.dumps(document)), "auto"))
            cue = result.cues[0]
            for field, value in (("segment_end_sample", 0), ("timing_basis", "invented"), ("words", [])):
                broken = deepcopy(document)
                broken["asr_evidence"]["cue_quality"][cue.cue_id]["alignment_drift"][field] = value
                self.assertIsNone(worker._language_checkpoint(result.cues, broken, "auto"), field)
            broken = deepcopy(document)
            broken["asr_evidence"]["cue_quality"][cue.cue_id].pop("alignment_drift")
            self.assertIsNone(worker._language_checkpoint(result.cues, broken, "auto"))
            replay, calls, instances = self.run_model(root, source=source)
            self.assertEqual((calls, instances), ([], []))
            self.assertEqual(replay, result)

    def test_silent_first_chunk_is_committed_without_pinning_its_false_language(self):
        with TemporaryDirectory() as directory:
            result, calls, _ = self.run_model(Path(directory), empty_first=True)
            self.assertEqual([call["language"] for call in calls], [None, None, "zh"])
            self.assertEqual((result.source_language, result.probability), ("zh", .9))
            self.assertEqual(len(result.asr_evidence["chunks"]), 3)
            self.assertEqual(len(result.cues), 2)

    def test_forged_coverage_and_legacy_transcript_are_not_reusable(self):
        with TemporaryDirectory() as directory:
            result, _, _ = self.run_model(Path(directory))
            doc = {"schema_version": 4, "source": "faster-whisper", "requested_source_language": "auto",
                   **result.language_metadata(), "asr_evidence": result.asr_evidence}
            self.assertIsNotNone(worker._language_checkpoint(result.cues, doc, "auto"))
            broken = deepcopy(doc)
            broken["asr_evidence"]["chunks"][1]["core_samples"][0] += 1
            self.assertIsNone(worker._language_checkpoint(result.cues, broken, "auto"))
            self.assertIsNone(worker._language_checkpoint(result.cues, {**doc, "schema_version": 3}, "auto"))
            broken = deepcopy(doc)
            broken["asr_evidence"]["chunk_binding"]["audio"] = "sha256:" + "f" * 64
            self.assertIsNone(worker._language_checkpoint(result.cues, broken, "auto"))
            self.assertIsNone(worker._language_checkpoint(result.cues, {**doc, "language_probability": .5}, "auto"))

    def test_backend_cannot_silently_override_explicit_source_language(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(worker.ProductionJobError, "SOURCE_LANGUAGE_MISMATCH"):
                self.run_model(Path(directory), language="zh-TW")
            self.assertFalse(list((Path(directory) / "asr-chunks").rglob("*.json")))

    def test_valid_payload_digest_cannot_admit_forged_word_timing(self):
        from engine.dubflow.worker.whisper_chunks import _digest
        with TemporaryDirectory() as directory:
            root = Path(directory)
            expected, _, _ = self.run_model(root)
            path = next(path for path in (root / "asr-chunks").rglob("*.json") if json.loads(path.read_text())["payload"]["chunk"]["index"] == 1)
            record = json.loads(path.read_text())
            record["payload"]["cues"][0]["start_ms"] += 1
            record["sha256"] = _digest(record["payload"])
            path.write_text(json.dumps(record), encoding="utf-8")
            actual, calls, _ = self.run_model(root)
            self.assertEqual(len(calls), 1)
            self.assertEqual(actual.cues, expected.cues)

    def boundary(self, right_text="Hello"):
        base = TimeBase(1, SAMPLE_RATE)
        chunks = ChunkPlanner(AdapterConfig(2 * SAMPLE_RATE, SAMPLE_RATE)).plan(
            TimeInterval(TimePoint(0, base), TimePoint(4 * SAMPLE_RATE, base)), input_hash="sha256:" + "b" * 64)
        def cue(words):
            return cue_from_words([{"text": text, "start_sample": start, "end_sample": end, "probability": probability}
                                   for text, start, end, probability in words], {key: None for key in ("avg_logprob", "no_speech_prob", "compression_ratio", "temperature")})
        left = cue([("Hello", 27200, 33600, .7)])
        right = cue([(right_text, 30400, 36800, .99), (" world", 36800, 44800, .8)])
        return reconcile(chunks, [{"cues": [asdict(left)]}, {"cues": [asdict(right)]}])

    def test_boundary_duplicate_keeps_original_score_and_preserves_both_origins(self):
        cues, reviews = self.boundary()
        self.assertEqual([cue.text for cue in cues], ["Hello world"])
        self.assertEqual(cues[0].confidence, .7)
        self.assertEqual(len(cues[0].evidence["chunk_origins"]), 2)
        self.assertEqual(reviews, [])

    def test_distinct_boundary_hypotheses_remain_visible_review_data(self):
        cues, reviews = self.boundary("Other")
        self.assertEqual([cue.text for cue in cues], ["Hello", "Other world"])
        self.assertEqual(reviews[0]["reason"], "chunk-boundary-disagreement")

    def test_zero_length_word_stays_in_a_positive_owned_speech_group(self):
        chunks = plan_chunks(30 * SAMPLE_RATE, "sha256:" + "c" * 64)
        source = speech("Hello almost world", [word("Hello", 20, 20.3), word(" almost", 20.3, 20.3), word(" world", 20.4, 21)])
        aligned, = segment_cues(source, 30 * SAMPLE_RATE)
        cues, reviews = reconcile(chunks, [{"cues": [asdict(aligned)]}])
        self.assertEqual(cues[0].text, "Hello almost world")
        self.assertEqual(cues[0].evidence["words"][1]["start_sample"], cues[0].evidence["words"][1]["end_sample"])
        self.assertEqual(reviews, [])


if __name__ == "__main__":
    unittest.main()

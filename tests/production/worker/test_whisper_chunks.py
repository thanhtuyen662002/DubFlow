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

    def run_model(self, root, *, language="auto", on_chunk=None, binding="model-v1", empty_first=False):
        (root / "asr/faster-whisper-small").mkdir(parents=True, exist_ok=True)
        path = root / "source.wav"
        if not path.exists():
            with wave.open(str(path), "wb") as writer:
                writer.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                for second in range(250):
                    writer.writeframes(int(second).to_bytes(2, "little", signed=True) * SAMPLE_RATE)
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
                return iter([speech()]), SimpleNamespace(language=observed, language_probability=.9)
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
            result = worker._transcribe_with_faster_whisper(path, root, language, on_chunk=on_chunk, model_binding=binding)
        return result, calls, instances

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
            _, _, _ = self.run_model(root)
            old = {path: path.read_bytes() for path in (root / "asr-chunks").rglob("*.json")}
            _, calls, _ = self.run_model(root, binding="model-v2")
            self.assertEqual(len(calls), 3)
            _, calls, _ = self.run_model(root, language="en")
            self.assertEqual(len(calls), 3)
            for path, content in old.items():
                self.assertEqual(path.read_bytes(), content)

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

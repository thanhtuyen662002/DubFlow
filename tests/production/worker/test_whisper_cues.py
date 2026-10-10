from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
import wave
from unittest.mock import patch

from engine.dubflow.worker import production_job as worker
from engine.dubflow.worker.whisper_cues import ASR_RECIPE, segment_cues


def word(text, start, end, probability=0.8):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def segment(text, words, start=120.0, end=139.0):
    return SimpleNamespace(text=text, words=words, start=start, end=end,
                           avg_logprob=-0.28, no_speech_prob=0.23, compression_ratio=1.47, temperature=0.0)


class WordAlignedCuesTests(unittest.TestCase):
    def test_movie_phrase_uses_words_after_long_leading_silence(self):
        source = segment(" Good night, skis.", [word(" Good", 137.88, 138.02, 0.526), word(" night,", 138.02, 138.22, 0.954), word(" skis.", 138.40, 138.62, 0.285)], start=122.02, end=138.66)
        cue, = segment_cues(source, 150 * 16000)
        self.assertEqual((cue.start_ms, cue.end_ms), (137880, 138620))
        self.assertEqual(cue.text, "Good night, skis.")
        self.assertEqual(cue.confidence, 0.285)
        self.assertEqual(cue.evidence["confidence_basis"], "minimum-word-probability-uncalibrated")
        self.assertEqual(cue.evidence["raw_segment_scores"]["avg_logprob"], -0.28)

    def test_long_pause_splits_without_dropping_either_phrase(self):
        source = segment("Hey, sit still. Good night.", [word("Hey,", 120.14, 120.66), word(" sit", 120.88, 121.24), word(" still.", 121.24, 121.70), word(" Good", 137.88, 138.02), word(" night.", 138.02, 138.22)])
        cues = segment_cues(source, 150 * 16000)
        self.assertEqual([(cue.text, cue.start_ms, cue.end_ms) for cue in cues], [("Hey, sit still.", 120140, 121700), ("Good night.", 137880, 138220)])

    def test_gap_threshold_uses_conservative_integer_samples(self):
        for gap_start, expected in ((2.2, 1), (2.20001, 1), (2.2001, 2)):
            with self.subTest(gap_start=gap_start):
                cues = segment_cues(segment("A B", [word("A", 1, 1.2), word(" B", gap_start, 2.5)], 1, 3), 4 * 16000)
                self.assertEqual(len(cues), expected)

    def test_zero_length_punctuation_is_kept(self):
        cue, = segment_cues(segment("A.", [word("A", 1, 1.2), word(".", 1.2, 1.2)], 1, 2), 4 * 16000)
        self.assertEqual((cue.text, cue.start_ms, cue.end_ms), ("A.", 1000, 1200))

    def test_missing_or_malformed_words_preserve_full_text_as_review_data(self):
        for words in (None, [], [word("A", 1, 1.2)], [word("A", 1, 1.2), word(" B", -1, 2)], [word("A", 1, 1.2), word(" B", 2, 8)], [word("A", 1, 1), word(" B", 3, 3)]):
            with self.subTest(words=words):
                cue, = segment_cues(segment("A B", words, 1, 4), 5 * 16000)
                self.assertEqual((cue.text, cue.start_ms, cue.end_ms, cue.confidence), ("A B", 1000, 4000, 0))
                self.assertEqual(cue.evidence["timing_basis"], "segment-fallback")
                self.assertEqual(cue.evidence["confidence_basis"], "unavailable")
                self.assertIsNotNone(cue.evidence["review_reason"])

    def test_unavailable_probability_never_becomes_fabricated_score(self):
        for probability in (None, True, float("nan"), -0.1, 1.1):
            with self.subTest(probability=probability):
                cue, = segment_cues(segment("A", [word("A", 1, 1.2, probability)], 1, 2), 4 * 16000)
                self.assertEqual(cue.confidence, 0)
                self.assertEqual(cue.evidence["timing_basis"], "word-alignment")
                self.assertEqual(cue.evidence["confidence_basis"], "unavailable")
        cue, = segment_cues(segment("A", [word("A", 1, 1.2, 0)], 1, 2), 4 * 16000)
        self.assertEqual(cue.evidence["confidence_basis"], "minimum-word-probability-uncalibrated")

    def test_fractional_sample_mapping_and_identity_ignore_segment_padding(self):
        words = [word("A", 1.00001, 1.20001)]
        first, = segment_cues(segment("A", words, 0, 3), 4 * 16000)
        second, = segment_cues(segment("A", words, 1, 2), 4 * 16000)
        self.assertEqual((first.start_ms, first.end_ms), (1000, 1201))
        self.assertEqual(first.cue_id, second.cue_id)

    def test_sdk_real_scalars_are_normalized_to_plain_sample_ticks_and_scores(self):
        class SdkReal(float):
            pass
        source = segment("A", [word("A", SdkReal(1.00001), SdkReal(1.20001), SdkReal(0.3))], SdkReal(1), SdkReal(2))
        source.avg_logprob = SdkReal(-0.2)
        cue, = segment_cues(source, 4 * 16000)
        self.assertEqual((cue.start_ms, cue.end_ms, cue.confidence), (1000, 1201, 0.3))
        self.assertIs(type(cue.evidence["raw_segment_scores"]["avg_logprob"]), float)
        self.assertIs(type(cue.evidence["start_sample"]), int)

    def test_impossible_segment_is_rejected_instead_of_inventing_duration(self):
        for start, end in ((2, 2), (5, 6), (True, 2), (float("nan"), 2)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                segment_cues(segment("A", None, start, end), 4 * 16000)

    def test_model_evidence_survives_checkpoint_and_rejects_tampering(self):
        source = segment("A", [word("A", 1.00001, 1.20001, 0.3)], 1, 2)
        calls = []
        class Model:
            def __init__(self, *args, **kwargs):
                calls.append(kwargs)
            def transcribe(self, *args, **kwargs):
                calls.append(kwargs)
                return iter([source]), SimpleNamespace(language="en", language_probability=0.98)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "asr/faster-whisper-small").mkdir(parents=True)
            audio_path = root / "audio.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(4 * 16000 * 2))
            with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=Model)}):
                result = worker._transcribe_with_faster_whisper(audio_path, root, "auto")
            document = {"schema_version": 4, "source": "faster-whisper", "requested_source_language": "auto", **result.language_metadata(), "asr_evidence": result.asr_evidence}
            restored = worker._language_checkpoint(result.cues, json.loads(json.dumps(document)), "auto")
            self.assertEqual(restored.asr_evidence, result.asr_evidence)
            self.assertEqual(restored.cues, result.cues)
            cue = result.cues[0]
            for key, value in (("start_sample", 0), ("confidence_basis", "calibrated"), ("review_reason", "invented"), ("words", [{"text": "A", "start_sample": 0, "end_sample": 19201, "probability": 0.3}])):
                broken = deepcopy(document)
                broken["asr_evidence"]["cue_quality"][cue.cue_id][key] = value
                self.assertIsNone(worker._language_checkpoint(result.cues, broken, "auto"), key)
            for change in ({"asr_evidence": None}, {"schema_version": 2}):
                self.assertIsNone(worker._language_checkpoint(result.cues, {**document, **change}, "auto"))
            self.assertEqual(set(cue.to_dict()), {"cue_id", "start_ms", "end_ms", "source_text", "translated_text", "confidence"})
            manifest_path = worker._write_manifest(root, SimpleNamespace(job_id="qa", source_path=audio_path, target_language="vi", enable_dubbing=False), SimpleNamespace(to_dict=lambda: {}), result.cues, {}, (), asr=result.asr_evidence)
            self.assertEqual(json.loads(manifest_path.read_text())["asr"], result.asr_evidence)
        self.assertTrue(calls[0]["local_files_only"])
        self.assertTrue(calls[1]["word_timestamps"])
        self.assertNotIn("hotwords", calls[1])
        self.assertEqual(result.asr_evidence["recipe"], ASR_RECIPE)


if __name__ == "__main__":
    unittest.main()

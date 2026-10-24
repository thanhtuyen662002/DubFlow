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
from engine.dubflow.worker.whisper_cues import ASR_RECIPE, MAX_DRIFT_CUE_SAMPLES, SAMPLE_RATE, segment_cues


def word(text, start, end, probability=0.8):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def segment(text, words, start=120.0, end=139.0):
    return SimpleNamespace(text=text, words=words, start=start, end=end,
                           avg_logprob=-0.28, no_speech_prob=0.23, compression_ratio=1.47, temperature=0.0)


class WordAlignedCuesTests(unittest.TestCase):
    def test_recorded_elephants_dream_drift_keeps_sentence_through_drifted_word(self):
        source = segment("Are you hurt?", [word("Are", 27.43, 27.97, .33), word(" you", 27.97, 33.19), word(" hurt?", 47.17, 47.47)], 27.43, 47.47)
        cue, = segment_cues(source, 50 * SAMPLE_RATE)
        self.assertEqual((cue.text, cue.start_ms, cue.end_ms), ("Are you hurt?", 27430, 33190))
        self.assertLessEqual(cue.evidence["end_sample"] - cue.evidence["start_sample"], MAX_DRIFT_CUE_SAMPLES)
        self.assertEqual(cue.evidence["review_reason"], "word-alignment-drift")
        self.assertEqual((cue.confidence, cue.evidence["confidence_basis"]), (0, "unavailable"))
        self.assertEqual(cue.evidence["timing_basis"], "drift-fallback")
        self.assertEqual(cue.evidence["alignment_drift"]["timing_basis"], "through-drifted-word")
        observed = cue.evidence["alignment_drift"]["words"]
        self.assertEqual([(item["start_sample"], item["end_sample"]) for item in observed], [(438880, 447520), (447520, 531040), (754720, 759520)])
        self.assertEqual(observed[0]["probability"], .33)

    def test_drift_fallback_is_clipped_to_original_segment_with_integer_offset(self):
        source = segment("Are you hurt?", [word("Are", 27.43, 27.97, .33), word(" you", 27.97, 33.19), word(" hurt?", 47.17, 47.47)], 27.5, 29)
        cue, = segment_cues(source, 50 * SAMPLE_RATE, sample_offset=1_234_567)
        self.assertEqual((cue.evidence["start_sample"], cue.evidence["end_sample"]), (1_234_567 + 440000, 1_234_567 + 464000))
        self.assertEqual(cue.evidence["alignment_drift"]["timing_basis"], "segment-bounded")

    def test_unbounded_or_empty_prefix_keeps_both_fragments_as_review_data(self):
        for first in ([word("Are", 27, 45)], [word("Are", 27, 27), word(" you", 27, 45)]):
            with self.subTest(first=first):
                text = "".join(item.word for item in first) + " hurt?"
                source = segment(text, first + [word(" hurt?", 47.17, 47.47)], 27, 48)
                cues = segment_cues(source, 50 * SAMPLE_RATE)
                self.assertEqual(len(cues), 2)
                self.assertEqual(" ".join(cue.text for cue in cues), text)
                self.assertTrue(all(cue.evidence["review_reason"] == "word-alignment-drift" for cue in cues))
                self.assertTrue(all(cue.evidence["timing_basis"] == "word-alignment" for cue in cues))

    def test_legitimate_three_second_pause_preserves_complete_sentences(self):
        source = segment("Hello. How are you?", [word("Hello.", 1, 1.5), word(" How", 4.5, 4.8), word(" are", 4.8, 5), word(" you?", 5, 5.2)], 1, 6)
        cues = segment_cues(source, 6 * SAMPLE_RATE)
        self.assertEqual([(cue.text, cue.start_ms, cue.end_ms) for cue in cues], [("Hello.", 1000, 1500), ("How are you?", 4500, 5200)])
        self.assertTrue(all(cue.evidence["review_reason"] is None for cue in cues))

    def test_terminal_previous_group_preserves_pause_even_with_stretched_word(self):
        for punctuation in (".", "!", "?", "。", "！", "？", "…", "。”"):
            with self.subTest(punctuation=punctuation):
                source = segment("Hello" + punctuation + " next?", [word("Hello" + punctuation, 1, 4), word(" next?", 7, 7.5)], 1, 8)
                cues = segment_cues(source, 8 * SAMPLE_RATE)
                self.assertEqual([cue.text for cue in cues], ["Hello" + punctuation, "next?"])
                self.assertEqual(cues[0].evidence["review_reason"], "word-alignment-drift")
                self.assertIsNone(cues[1].evidence["review_reason"])

    def test_capitalised_new_sentence_and_non_orphan_tails_preserve_pause(self):
        for tail in ([word(" Hurt?", 7, 7.5)], [word(" hurt", 7, 7.5)],
                     [word(" is", 7, 7.1), word(" this", 7.1, 7.2), word(" really", 7.2, 7.3), word(" true?", 7.3, 7.5)]):
            with self.subTest(tail=tail):
                text = "Wait" + "".join(item.word for item in tail)
                cues = segment_cues(segment(text, [word("Wait", 1, 4)] + tail, 1, 8), 8 * SAMPLE_RATE)
                self.assertEqual(len(cues), 2)
                self.assertIsNone(cues[1].evidence["review_reason"])

    def test_chinese_continuation_uses_terminal_punctuation_without_capitalisation(self):
        for punctuation in ("。", "！", "？", "…"):
            with self.subTest(punctuation=punctuation):
                source = segment("你受伤了吗" + punctuation, [word("你", 1, 1.2), word("受伤", 1.2, 4), word("了吗" + punctuation, 9, 9.5)], 1, 10)
                cue, = segment_cues(source, 10 * SAMPLE_RATE)
                self.assertEqual((cue.text, cue.start_ms, cue.end_ms), (source.text, 1000, 9500))
                self.assertEqual(cue.evidence["review_reason"], "word-alignment-drift")

    def test_orphan_signal_alone_preserves_sentence_and_word_duration_threshold_is_strict(self):
        cue, = segment_cues(segment("Are you hurt?", [word("Are", 1, 1.2), word(" you", 1.2, 1.5), word(" hurt?", 4.5, 4.8)], 1, 5), 5 * SAMPLE_RATE)
        self.assertEqual((cue.text, cue.evidence["review_reason"]), ("Are you hurt?", "word-alignment-drift"))
        for end, reason in ((3.5, None), (3.5001, "word-alignment-drift")):
            with self.subTest(end=end):
                cue, = segment_cues(segment("Hello.", [word("Hello.", 1, end)], 1, 4), 4 * SAMPLE_RATE)
                self.assertEqual(cue.evidence["review_reason"], reason)
                self.assertEqual((cue.confidence, cue.evidence["timing_basis"]), (.8, "word-alignment"))
        self.assertEqual(ASR_RECIPE, "faster-whisper-1.2.1-bounded-word-gap-v4")

    def test_movie_phrase_uses_words_after_long_leading_silence(self):
        source = segment(" Good night, skis.", [word(" Good", 137.88, 138.02, 0.526), word(" night,", 138.02, 138.22, 0.954), word(" skis.", 138.40, 138.62, 0.285)], start=122.02, end=138.66)
        cue, = segment_cues(source, 150 * 16000)
        self.assertEqual((cue.start_ms, cue.end_ms), (137880, 138620))
        self.assertEqual(cue.text, "Good night, skis.")
        self.assertEqual(cue.confidence, 0.285)
        self.assertEqual(cue.evidence["confidence_basis"], "minimum-word-probability-uncalibrated")
        self.assertEqual(cue.evidence["raw_segment_scores"]["avg_logprob"], -0.28)
        self.assertIsNone(cue.evidence["review_reason"])

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
            from engine.dubflow.media.adapter import MediaStream, Rational
            probe = SimpleNamespace(video=MediaStream(0, "video", "h264", Rational(1, 16000), 0, 16000, 16000), to_dict=lambda: {})
            manifest_path = worker._write_manifest(root, SimpleNamespace(job_id="qa", source_path=audio_path, target_language="vi", enable_dubbing=False), probe, result.cues, {}, (), asr=result.asr_evidence)
            self.assertEqual(json.loads(manifest_path.read_text())["asr"], result.asr_evidence)
        self.assertTrue(calls[0]["local_files_only"])
        self.assertTrue(calls[1]["word_timestamps"])
        self.assertNotIn("hotwords", calls[1])
        self.assertEqual(result.asr_evidence["recipe"], ASR_RECIPE)


if __name__ == "__main__":
    unittest.main()

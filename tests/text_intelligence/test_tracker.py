from __future__ import annotations

import json
import unittest

from engine.dubflow.ocr import BenchmarkReport, BBox, OcrObservation, TextIntelligence


def item(identifier: str, frame: int, start: int, end: int, text: str, box: BBox, **kwargs) -> OcrObservation:
    return OcrObservation(identifier, frame, start, end, text, box, "0.95", **kwargs)


class TextIntelligenceTests(unittest.TestCase):
    def test_horizontal_and_multi_text_observations_keep_temporal_identity(self) -> None:
        tracker = TextIntelligence()
        document = tracker.track("job-text", [
            item("dialogue-0", 0, 0, 100, "你好", BBox(100, 800, 300, 60), asr_text="你好", asr_utterance_id="u1"),
            item("dialogue-1", 1, 100, 200, "你好", BBox(105, 802, 300, 60), asr_text="你好", asr_utterance_id="u1"),
            item("watermark-0", 0, 0, 200, "logo.example.com", BBox(10, 10, 180, 30), role_hint="watermark"),
            item("danmaku-0", 0, 50, 150, "弹幕", BBox(500, 400, 150, 40), role_hint="danmaku"),
        ])
        self.assertEqual(len(document.tracks), 3)
        dialogue = next(track for track in document.tracks if track["role"] == "dialogue")
        self.assertEqual(len(dialogue["segments"]), 2)
        self.assertEqual(next(track for track in document.tracks if track["role"] == "watermark")["decision"], "remove")
        self.assertEqual(next(track for track in document.tracks if track["role"] == "danmaku")["decision"], "remove")

    def test_asr_ocr_conflict_is_provenanced_and_blocks_removal(self) -> None:
        document = TextIntelligence().track("job-conflict", [item("o1", 0, 0, 100, "看水印", BBox(0, 0, 100, 20), role_hint="watermark", asr_text="你好", asr_utterance_id="asr-1")])
        self.assertEqual(len(document.conflicts), 1)
        self.assertEqual(document.conflicts[0]["reason"], "ASR_OCR_CONFLICT")
        self.assertEqual(document.tracks[0]["decision"], "review")

    def test_uncertain_role_never_auto_removes_and_debug_overlay_is_hashed(self) -> None:
        document = TextIntelligence().track("job-uncertain", [item("o1", 0, 0, 100, "???", BBox(10, 10, 20, 20))])
        self.assertEqual(document.tracks[0]["role"], "unknown")
        self.assertEqual(document.tracks[0]["decision"], "review")
        self.assertTrue(document.debug_overlay["content_hash"].startswith("sha256:"))
        json.dumps(document.to_dict(), ensure_ascii=False)

    def test_machine_readable_benchmark_decision_and_cer(self) -> None:
        promoted = BenchmarkReport.from_labels("dataset-1", ["dialogue", "watermark"], ["dialogue", "watermark"], ["你好", "logo"], ["你好", "logo"])
        self.assertEqual(promoted.decision, "PROMOTE")
        self.assertEqual(promoted.to_dict()["cer"], "0")
        experimental = BenchmarkReport.from_labels("dataset-1", ["dialogue", "watermark"], ["unknown", "watermark"], ["你好", "logo"], ["bad", "logo"])
        self.assertIn(experimental.decision, {"EXPERIMENTAL", "REJECT"})

    def test_invalid_ticks_and_bbox_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            item("bad", 0, 2, 1, "x", BBox(0, 0, 1, 1))
        with self.assertRaises(ValueError):
            BBox(0, 0, 0, 1)


if __name__ == "__main__":
    unittest.main()

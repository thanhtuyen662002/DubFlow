from __future__ import annotations

from types import SimpleNamespace
import unittest

from engine.dubflow.subtitle.cleanup import plan_cleanup, verify_cleaned_media


def _document() -> dict[str, object]:
    return {
        "tracks": [
            {
                "track_id": "txt-0123456789abcdef",
                "role": "dialogue",
                "decision": "keep",
                "confidence": "0.95",
                "segments": [
                    {"observation_id": "one", "start_ticks": "0", "end_ticks": "1000", "bbox": {"x": 10, "y": 20, "width": 100, "height": 30}, "confidence": "0.95"},
                    {"observation_id": "two", "start_ticks": "1050", "end_ticks": "2000", "bbox": {"x": 12, "y": 21, "width": 100, "height": 30}, "confidence": "0.94"},
                ],
            },
            {
                "track_id": "txt-fedcba9876543210",
                "role": "watermark",
                "decision": "remove",
                "confidence": "1",
                "segments": [{"observation_id": "wm", "start_ticks": "0", "end_ticks": "2000", "bbox": {"x": 2, "y": 2, "width": 100, "height": 30}, "confidence": "1"}],
            },
        ],
        "conflicts": [],
    }


class ProductionCleanupTests(unittest.TestCase):
    def test_only_high_confidence_dialogue_is_masked_and_adjacent_segments_smooth(self) -> None:
        plan = plan_cleanup(_document(), width=640, height=360)
        self.assertEqual(plan.backend_id, "vis-1-cover")
        self.assertTrue(plan.auto_remove)
        self.assertEqual(len(plan.masks), 1)
        self.assertEqual((plan.masks[0].start_ticks, plan.masks[0].end_ticks), (0, 2000))
        self.assertEqual(plan.fallback_chain[-1], "vis-0-source")

    def test_uncertain_or_conflicting_dialogue_retains_source(self) -> None:
        document = _document()
        document["tracks"][0]["confidence"] = "0.5"  # type: ignore[index]
        plan = plan_cleanup(document, width=640, height=360)
        self.assertEqual(plan.backend_id, "vis-0-source")
        self.assertFalse(plan.auto_remove)
        self.assertIn("retained source text", " ".join(plan.warnings))

    def test_container_verification_rejects_audio_loss_and_shortening(self) -> None:
        source = SimpleNamespace(duration_ticks=10_000, has_audio=True, video=SimpleNamespace(codec_name="h264"))
        cleaned = SimpleNamespace(duration_ticks=7_000, has_audio=False, video=SimpleNamespace(codec_name="h264"))
        result = verify_cleaned_media(source, cleaned)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["reasons"]), 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from decimal import Decimal
import unittest

from engine.dubflow.visual_identity import ActiveSpeakerAssociator, BBox, CharacterBenchmark, VisualCharacterTracker, VisualDetection


def detection(identifier: str, scene: str, frame: int, key: str | None, actor: str | None, x: int, *, lip: str = "0.8") -> VisualDetection:
    return VisualDetection(identifier, scene, frame, frame * 100, frame * 100 + 100, BBox(x, 100, 100, 200), (1.0, 0.0), lip, "0.9", key, actor)


class CharacterSpeakerTests(unittest.TestCase):
    def test_same_character_returns_across_scenes_but_two_visual_characters_stay_distinct(self) -> None:
        detections = [
            detection("c1-a", "scene-1", 0, "char-a", "actor-1", 10),
            detection("c1-b", "scene-2", 1, "char-a", "actor-1", 400),
            detection("c2", "scene-1", 2, "char-b", "actor-1", 700),
        ]
        tracks = VisualCharacterTracker().build("job-character", detections)
        self.assertEqual(len(tracks), 2)
        self.assertEqual({track["actor_group_id"] for track in tracks}, {"actor-1"})
        self.assertEqual(len(next(track for track in tracks if track["character_key"] == "char-a")["segments"]), 2)

    def test_visible_active_speaker_requires_margin(self) -> None:
        associator = ActiveSpeakerAssociator()
        visible = associator.associate("spk-a", 0, 100, {"char-1": "0.92", "char-2": "0.3"})
        self.assertEqual(visible.status, "visible")
        self.assertEqual(visible.visual_track_id, "char-1")
        ambiguous = associator.associate("spk-a", 0, 100, {"char-1": "0.75", "char-2": "0.72"})
        self.assertEqual(ambiguous.status, "unresolved")
        self.assertIsNone(ambiguous.visual_track_id)

    def test_listener_visible_offscreen_and_fallback_cluster(self) -> None:
        association = ActiveSpeakerAssociator().associate("spk-narrator", 0, 100, {}, offscreen=True)
        self.assertEqual(association.status, "offscreen")
        self.assertEqual(association.fallback_audio_cluster_id, "spk-narrator")

    def test_benchmark_metrics_are_serializable(self) -> None:
        benchmark = CharacterBenchmark(1, Decimal("0.9"), Decimal("1"), Decimal("0.85"), 40, 4096)
        self.assertEqual(benchmark.to_dict()["offscreen_precision"], "1")

    def test_invalid_detection_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            detection("bad", "scene", 0, None, None, 0, lip="1.5")


if __name__ == "__main__":
    unittest.main()

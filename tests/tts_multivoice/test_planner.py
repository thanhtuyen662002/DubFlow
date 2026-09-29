from __future__ import annotations

import unittest

from engine.dubflow.tts.casting import TtsCue, VoiceCaster, VoiceOption
from engine.dubflow.tts.duration import DurationPolicy


class MultiVoicePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.voices = [VoiceOption("voice-a"), VoiceOption("voice-b"), VoiceOption("voice-narrator", narrator=True)]
        self.caster = VoiceCaster(self.voices, duration_policy=DurationPolicy(max_speed_ratio_milli=1250))

    def test_cluster_voice_mapping_is_deterministic_and_consistent(self) -> None:
        cues = [
            TtsCue("b-late", "spk-b", "speaker", 100, 200, "B"),
            TtsCue("a-early", "spk-a", "speaker", 0, 100, "A"),
            TtsCue("a-late", "spk-a", "speaker", 200, 300, "A2"),
        ]
        first = self.caster.assign(cues)
        second = self.caster.assign(tuple(reversed(cues)))
        by_id_first = {item.segment_id: item.voice_id for item in first}
        by_id_second = {item.segment_id: item.voice_id for item in second}
        self.assertEqual(by_id_first, by_id_second)
        self.assertEqual(by_id_first["a-early"], by_id_first["a-late"])

    def test_overlapping_speakers_use_separate_tracks_and_narrator_fallback(self) -> None:
        cues = [
            TtsCue("left", "spk-left", "speaker", 0, 100, "left"),
            TtsCue("right", "spk-right", "speaker", 50, 150, "right"),
            TtsCue("narrator", None, "narrator", 25, 75, "narrator"),
        ]
        plan = self.caster.plan(cues, {"left": 100, "right": 100, "narrator": 50})
        assignments = {item.segment_id: item for item in plan.assignments}
        self.assertNotEqual(assignments["left"].track_id, assignments["right"].track_id)
        self.assertEqual(assignments["narrator"].voice_id, "voice-narrator")
        self.assertEqual(len(plan.assignments), 3)

        one_voice = VoiceCaster([VoiceOption("shared")])
        shared_plan = one_voice.plan(cues[:2], {"left": 100, "right": 100})
        self.assertEqual({item.voice_id for item in shared_plan.assignments}, {"shared"})
        self.assertEqual(len({item.track_id for item in shared_plan.assignments}), 2)

    def test_duration_policy_prefers_padding_then_bounded_speed_then_rewrite(self) -> None:
        padded = self.caster.duration_policy.fit("padded", 0, 1000, 900)
        self.assertEqual(padded.fit_mode, "padded")
        adjusted = self.caster.duration_policy.fit("adjusted", 0, 1000, 1100)
        self.assertEqual(adjusted.fit_mode, "speed_adjusted")
        self.assertEqual(adjusted.speed_ratio_milli, 1100)
        rewrite = self.caster.duration_policy.fit("rewrite", 0, 1000, 2000, rewritten_text="short", rewritten_duration_ticks=1100)
        self.assertEqual(rewrite.fit_mode, "speed_adjusted")
        required = self.caster.duration_policy.fit("required", 0, 1000, 2000)
        self.assertEqual(required.fit_mode, "rewrite_required")
        self.assertEqual(required.speed_ratio_milli, 1250)

    def test_integer_boundary_and_invalid_duration_are_rejected(self) -> None:
        fit = self.caster.duration_policy.fit("ceil", 0, 8, 9)
        self.assertEqual(fit.speed_ratio_milli, 1125)
        with self.assertRaises(ValueError):
            self.caster.duration_policy.fit("zero", 10, 10, 1)
        with self.assertRaises(ValueError):
            TtsCue("bad", "spk", "speaker", 0, 1, "\n")


if __name__ == "__main__":
    unittest.main()

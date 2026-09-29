from __future__ import annotations

import json
import unittest

from engine.dubflow.separation import AudioFixture, AudioSeparationBenchmark, BackendResult


class AudioCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = AudioFixture("speech-music-sfx", 90_000 * 120, 12_000, 10_000, 9_000, True)
        self.policy = AudioSeparationBenchmark()

    def test_candidate_metrics_select_quality_backend_and_preserve_background(self) -> None:
        results = [
            BackendResult("aud-1", self.fixture.fixture_id, 8_000, 2_500, 120, 2_000, 64_000_000, True),
            BackendResult("aud-2", self.fixture.fixture_id, 9_000, 2_000, 180, 3_000, 128_000_000, True),
        ]
        plan = self.policy.evaluate(self.fixture, results)
        self.assertEqual(plan.backend_id, "aud-1")
        self.assertIn(plan.decision, {"PROMOTE", "EXPERIMENTAL"})
        self.assertTrue(plan.preserve_music_sfx)
        self.assertFalse(plan.fallback_used)

    def test_failed_or_artifact_heavy_backends_use_aud0_without_blocking(self) -> None:
        results = [
            BackendResult("broken", self.fixture.fixture_id, 12_000, 1_000, 50, 100, 1_000, True, "MODEL_UNAVAILABLE"),
            BackendResult("artifact-heavy", self.fixture.fixture_id, 10_000, 1_000, 800, 100, 1_000, True),
        ]
        plan = self.policy.evaluate(self.fixture, results)
        self.assertEqual(plan.backend_id, "aud-0-conservative")
        self.assertEqual(plan.decision, "FALLBACK")
        self.assertTrue(plan.fallback_used)
        self.assertEqual(plan.residual_speech_millidb, self.fixture.speech_rms_millidb)

    def test_long_form_and_resource_metrics_are_machine_readable(self) -> None:
        result = BackendResult("aud-1", self.fixture.fixture_id, 8_000, 2_500, 120, 2_000, 64_000_000, True)
        plan = self.policy.evaluate(self.fixture, [result])
        report = self.policy.report([self.fixture], [result], [plan])
        json.dumps(report)
        self.assertEqual(report["resource_profile"]["max_latency_ms"], 2_000)
        self.assertTrue(report["content_hash"].startswith("sha256:"))

    def test_invalid_negative_or_non_boolean_metrics_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AudioFixture("bad", -1, 1, 1, 1)
        with self.assertRaises(ValueError):
            BackendResult("bad", "fixture", 1, 1, 1, 1, 1, "yes")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest

from engine.dubflow.inpaint import InpaintResult, VisualCleanupBenchmark, VisualCleanupFixture


class VisualCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = VisualCleanupFixture("background-safe", 240, 1080, 1920, 50_000, 20_000, 500_000)
        self.policy = VisualCleanupBenchmark()

    def test_candidate_metrics_select_inpaint_for_safe_background(self) -> None:
        result = InpaintResult("vis-1", self.fixture.fixture_id, 960_000, 10_000, 15_000, 5_000, 10_000, 64_000_000, True)
        plan = self.policy.evaluate(self.fixture, [result])
        self.assertEqual(plan.mode, "inpaint")
        self.assertTrue(plan.auto_remove)
        self.assertFalse(plan.fallback_used)

    def test_face_overlap_or_damage_uses_vis0_cover(self) -> None:
        risky = VisualCleanupFixture("face-risk", 240, 1080, 1920, 50_000, 300_000, 500_000)
        result = InpaintResult("vis-1", risky.fixture_id, 990_000, 1_000, 1_000, 1_000, 1_000, 64_000_000, True)
        plan = self.policy.evaluate(risky, [result])
        self.assertEqual(plan.backend_id, "vis-0-cover")
        self.assertEqual(plan.mode, "cover")
        self.assertFalse(plan.auto_remove)
        self.assertTrue(plan.fallback_used)
        self.assertIn("face", " ".join(plan.warnings))

    def test_failed_backend_and_temporal_flicker_do_not_block_batch(self) -> None:
        results = [
            InpaintResult("broken", self.fixture.fixture_id, 999_000, 1_000, 1_000, 1_000, 1_000, 1_000, True, "MODEL_UNAVAILABLE"),
            InpaintResult("flicker", self.fixture.fixture_id, 999_000, 1_000, 90_000, 1_000, 1_000, 1_000, True),
        ]
        plan = self.policy.evaluate(self.fixture, results)
        self.assertEqual(plan.mode, "cover")
        self.assertTrue(plan.fallback_used)

    def test_report_has_resource_profile_and_hash(self) -> None:
        result = InpaintResult("vis-1", self.fixture.fixture_id, 960_000, 10_000, 15_000, 5_000, 10_000, 64_000_000, True)
        plan = self.policy.evaluate(self.fixture, [result])
        report = self.policy.report([self.fixture], [result], [plan])
        json.dumps(report)
        self.assertEqual(report["resource_profile"]["max_peak_memory_bytes"], 64_000_000)
        self.assertTrue(report["content_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()

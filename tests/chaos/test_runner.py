from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.soak.runner import SimulatedCrash, SoakRunner, SoakScenario, run_release_rehearsal


class SoakRunnerTests(unittest.TestCase):
    def test_hard_kill_checkpoint_resumes_without_losing_work(self) -> None:
        scenario = SoakScenario(
            job_ids=("alpha", "beta"),
            steps_per_job=5,
            disk_budget=20,
            crash_job_id="alpha",
            crash_step=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SimulatedCrash):
                SoakRunner(root, scenario).run()
            state_after_crash = json.loads((root / "soak-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state_after_crash["jobs"]["alpha"]["current_step"], 2)
            evidence = SoakRunner(root, scenario).run()
            self.assertEqual(evidence.completed_jobs, ("alpha", "beta"))
            self.assertTrue(evidence.crash_recovered)
            self.assertEqual(evidence.resumed_steps, 2)

    def test_dependency_outage_does_not_stop_sibling_jobs(self) -> None:
        scenario = SoakScenario(
            job_ids=("good-a", "outage", "good-b"),
            steps_per_job=3,
            disk_budget=20,
            dependency_outage_job_id="outage",
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = SoakRunner(Path(directory), scenario).run()
            self.assertEqual(evidence.failed_jobs, ("outage",))
            self.assertEqual(evidence.completed_jobs, ("good-a", "good-b"))
            self.assertTrue(evidence.dependency_outage_isolated)

    def test_disk_pressure_isolated_and_machine_readable(self) -> None:
        scenario = SoakScenario(
            job_ids=("good", "full"),
            steps_per_job=4,
            disk_budget=20,
            disk_pressure_job_id="full",
            disk_pressure_step=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = SoakRunner(Path(directory), scenario).run()
            self.assertEqual(evidence.completed_jobs, ("good",))
            self.assertEqual(evidence.failed_jobs, ("full",))
            self.assertTrue(evidence.disk_pressure_observed)
            self.assertEqual(evidence.resource_profile["failure_count"], 1)

    def test_updater_failure_records_rollback_without_aborting_queue(self) -> None:
        scenario = SoakScenario(
            job_ids=("job",),
            steps_per_job=2,
            disk_budget=4,
            updater_failure=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = SoakRunner(Path(directory), scenario).run()
            self.assertEqual(evidence.completed_jobs, ("job",))
            self.assertTrue(evidence.updater_rollback_observed)

    def test_job_id_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            SoakScenario(job_ids=("../escape",), steps_per_job=1, disk_budget=1)
        with self.assertRaises(ValueError):
            SoakScenario(job_ids=("C:\\escape",), steps_per_job=1, disk_budget=1)

    def test_release_cli_profile_emits_reproducible_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "release-evidence.json"
            evidence = run_release_rehearsal(output)
            encoded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(encoded["schema_version"], 1)
            self.assertTrue(evidence.crash_recovered)
            self.assertGreater(evidence.resumed_steps, 0)
            self.assertTrue(evidence.disk_pressure_observed)
            self.assertTrue(evidence.dependency_outage_isolated)
            self.assertTrue(evidence.updater_rollback_observed)
            self.assertTrue(evidence.gpu_oom_recovered)
            self.assertTrue(evidence.corrupt_input_quarantined)
            self.assertTrue(evidence.dead_worker_recovered)
            self.assertTrue(evidence.artifact_hashes_verified)
            self.assertTrue(evidence.resumability_verified)
            self.assertEqual(evidence.resource_profile["queue_size"], 100)
            self.assertIn("long-form-01", evidence.completed_jobs)

    def test_five_hundred_item_queue_is_bounded_and_hashes_outputs(self) -> None:
        scenario = SoakScenario(
            job_ids=tuple(f"item-{index:03d}" for index in range(500)),
            steps_per_job=1,
            disk_budget=500,
            profile="queue-500",
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = SoakRunner(Path(directory), scenario).run()
            self.assertEqual(len(evidence.completed_jobs), 500)
            self.assertEqual(len(evidence.artifact_hashes), 500)
            self.assertTrue(evidence.artifact_hashes_verified)
            self.assertTrue(evidence.resumability_verified)


if __name__ == "__main__":
    unittest.main()

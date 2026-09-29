from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from scripts.watchdog.classifier import Check, PRSnapshot, classify_snapshot, parse_lease


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)


def load(name: str) -> dict:
    return json.loads((ROOT / "tests" / "watchdog" / "fixtures" / name).read_text(encoding="utf-8"))


def pr(data: dict, main_sha: str) -> PRSnapshot:
    return PRSnapshot(
        data["number"],
        data["title"],
        data["body"],
        data["head_sha"],
        data["base_sha"],
        main_sha,
        tuple(Check(item["name"], item["status"], item["conclusion"]) for item in data["checks"]),
    )


class WatchdogTests(unittest.TestCase):
    def test_watchdog_workflow_uses_an_active_repository_token_expression(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "watchdog.yml").read_text(encoding="utf-8")
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertNotIn(r"GH_TOKEN: \${{ github.token }}", workflow)

    def test_healthy_snapshot_has_no_warning(self) -> None:
        data = load("healthy.json")
        self.assertEqual(classify_snapshot(prs=[pr(data["prs"][0], data["main_sha"])], commits=[], main_protected=True, now=NOW), [])

    def test_stale_base_is_not_merge_ready(self) -> None:
        data = load("stale-base.json")
        codes = {item.code for item in classify_snapshot(prs=[pr(data["prs"][0], data["main_sha"])], commits=[], main_protected=True, now=NOW)}
        self.assertIn("STALE_BASE", codes)

    def test_stale_heartbeat_duplicate_claim_and_overlap_are_visible(self) -> None:
        healthy = load("healthy.json")
        first = pr(healthy["prs"][0], "b" * 40)
        body = healthy["prs"][0]["body"].replace("Lease-Heartbeat: 2026-09-29T02:59:00+00:00", "Lease-Heartbeat: 2026-09-28T23:00:00+00:00")
        second = PRSnapshot(43, "duplicate", body, "d" * 40, "b" * 40, "b" * 40, (Check("governance", "completed", "success"),))
        codes = {item.code for item in classify_snapshot(prs=[first, second], commits=[], main_protected=True, now=NOW)}
        self.assertTrue({"STALE_HEARTBEAT", "DUPLICATE_CLAIM", "CONFLICT_OVERLAP"} <= codes)

    def test_waiting_and_direct_main_warnings_are_distinct(self) -> None:
        data = load("healthy.json")
        pending = PRSnapshot(data["prs"][0]["number"], "pending", data["prs"][0]["body"], "c" * 40, "b" * 40, "b" * 40, ())
        warnings = classify_snapshot(prs=[pending], commits=[{"sha": "e" * 40, "message": "unreviewed push", "associated_pr": False}], main_protected=True, now=NOW)
        codes = {item.code for item in warnings}
        self.assertIn("WAITING_CI", codes)
        self.assertIn("DIRECT_MAIN_COMMIT", codes)

    def test_malformed_lease_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_lease("DUBFLOW_PR_V1\nIssue: #1")


if __name__ == "__main__":
    unittest.main()

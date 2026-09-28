from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "job_status" / "schema-v1.json"


class JobStatusContractTests(unittest.TestCase):
    def test_schema_has_explicit_liveness_states_and_decimal_progress(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        states = set(schema["properties"]["state"]["enum"])
        self.assertTrue({"RUNNING", "WAITING_RESOURCE", "WAITING_EXTERNAL", "RETRYING", "BLOCKED_NEEDS_ACTION"} <= states)
        progress = schema["properties"]["progress"]["properties"]
        pattern = re.compile(progress["completed_units"]["pattern"])
        self.assertTrue(pattern.fullmatch("0"))
        self.assertTrue(pattern.fullmatch(str(2**64 - 1)))
        self.assertFalse(pattern.fullmatch("01"))

    def test_resource_and_retry_fields_are_required_for_restart_reconstruction(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            set(schema["properties"]["retry"]["required"]),
            {"attempt", "max_attempts", "condition_fingerprint"},
        )
        self.assertEqual(
            set(schema["properties"]["resource"]["required"]),
            {"kind", "held", "release_requested"},
        )


if __name__ == "__main__":
    unittest.main()

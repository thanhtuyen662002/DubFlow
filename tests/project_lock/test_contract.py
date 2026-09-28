from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "project_lock" / "schema-v1.json"


class ProjectLockContractTests(unittest.TestCase):
    def test_schema_requires_supervisor_scope_and_process_start_token(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["role"]["const"], "supervisor")
        self.assertEqual(schema["properties"]["scope"]["const"], "project-db-artifacts-temp")
        self.assertIn("start_token", schema["required"])

    def test_numeric_wire_fields_are_canonical_and_bounded(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        pid_pattern = re.compile(schema["properties"]["pid"]["pattern"])
        epoch_pattern = re.compile(schema["properties"]["acquired_epoch_ms"]["pattern"])
        self.assertTrue(pid_pattern.fullmatch("1"))
        self.assertFalse(pid_pattern.fullmatch("0"))
        self.assertTrue(epoch_pattern.fullmatch("0"))
        self.assertFalse(epoch_pattern.fullmatch("01"))


if __name__ == "__main__":
    unittest.main()

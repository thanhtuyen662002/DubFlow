from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "scheduler" / "schema-v1.json"


class SchedulerContractTests(unittest.TestCase):
    def test_all_resource_classes_and_guards_are_versioned(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["resource_classes"]["minItems"], 6)
        self.assertEqual(schema["properties"]["disk_guard"]["properties"]["minimum_free_ratio_ppm"]["maximum"], 1_000_000)
        self.assertEqual(set(schema["properties"]["retention_budget"]["required"]), {"debug_bytes", "cache_bytes"})

    def test_wire_bytes_are_canonical_decimal_strings(self) -> None:
        pattern = re.compile(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]["disk_guard"]["properties"]["minimum_free_bytes"]["pattern"])
        self.assertTrue(pattern.fullmatch("0"))
        self.assertTrue(pattern.fullmatch(str(2**64 - 1)))
        self.assertFalse(pattern.fullmatch("01"))


if __name__ == "__main__":
    unittest.main()

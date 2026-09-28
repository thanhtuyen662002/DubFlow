from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "model_retention" / "schema-v1.json"


class ModelRetentionContractTests(unittest.TestCase):
    def test_exact_version_hash_and_non_terminal_pin_are_required(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), {"schema_version", "job_id", "terminal", "required_versions"})
        ref = schema["$defs"]["version_ref"]
        self.assertEqual(set(ref["required"]), {"id", "version", "sha256"})

    def test_hash_wire_format_is_lowercase_fixed_width(self) -> None:
        pattern = re.compile(json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["version_ref"]["properties"]["sha256"]["pattern"])
        self.assertTrue(pattern.fullmatch("a" * 64))
        self.assertFalse(pattern.fullmatch("A" * 64))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "artifacts" / "schema-v1.json"


class ArtifactGraphContractTests(unittest.TestCase):
    def test_provenance_fields_cover_all_cache_key_inputs(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        required = set(schema["properties"]["provenance"]["required"])
        self.assertEqual(required, {"producer_version", "input_hashes", "config_hash", "model_hash", "contract_version"})
        self.assertIn("QC", schema["properties"]["kind"]["enum"])

    def test_hashes_are_lowercase_fixed_width(self) -> None:
        pattern = re.compile(schema := json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]["provenance"]["properties"]["config_hash"]["pattern"])
        self.assertTrue(pattern.fullmatch("a" * 64))
        self.assertFalse(pattern.fullmatch("A" * 64))
        self.assertFalse(pattern.fullmatch("a" * 63))


if __name__ == "__main__":
    unittest.main()

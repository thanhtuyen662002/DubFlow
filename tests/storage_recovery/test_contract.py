"""Portable checks for the storage topology wire boundary.

The behavioral recovery matrix is exercised by the Rust crates; these checks
keep the JSON contract and its wide integer policy visible to the Python CI
lane as well.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "storage" / "schema-v1.json"


class StorageContractTests(unittest.TestCase):
    def test_schema_requires_exact_root_kinds_and_decimal_u64(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        roots = schema["properties"]["roots"]
        self.assertEqual(roots["minItems"], 6)
        self.assertEqual(roots["maxItems"], 6)
        required_kinds = {
            clause["contains"]["properties"]["kind"]["const"]
            for clause in roots["allOf"]
        }
        self.assertEqual(required_kinds, {"control", "source", "output", "model", "cache", "temp"})
        uint64 = schema["$defs"]["uint64_string"]
        pattern = re.compile(uint64["pattern"])
        self.assertTrue(pattern.fullmatch("0"))
        self.assertTrue(pattern.fullmatch(str(2**64 - 1)))
        self.assertFalse(pattern.fullmatch("01"))
        def parse_u64_wire(value: str) -> int:
            if not pattern.fullmatch(value) or int(value) > 2**64 - 1:
                raise ValueError(value)
            return int(value)

        with self.assertRaises(ValueError):
            parse_u64_wire("99999999999999999999")

    def test_locator_path_pattern_blocks_absolute_and_parent_paths(self) -> None:
        pattern = re.compile(
            schema_pattern := json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]["artifact_identity"]["properties"]["relative_path"]["pattern"]
        )
        for value in ("C:\\video.mp4", "\\\\server\\share\\video.mp4", "../video.mp4", "..\\video.mp4"):
            self.assertIsNone(pattern.fullmatch(value), value)
        self.assertIsNotNone(pattern.fullmatch("字幕/片段.mp4"), schema_pattern)


if __name__ == "__main__":
    unittest.main()

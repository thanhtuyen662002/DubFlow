from __future__ import annotations

import json
from pathlib import Path
import unittest

from catalog_validator import CatalogError, load_catalog, validate_catalog


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "fixtures" / "catalog" / "catalog-v1.json"


class CatalogTests(unittest.TestCase):
    def test_catalog_covers_required_edge_cases(self) -> None:
        catalog = load_catalog(CATALOG, verify_generated_assets=False)
        self.assertEqual(catalog["schema_version"], 1)
        self.assertGreaterEqual(len(catalog["fixtures"]), 8)

    def test_generated_catalog_is_hash_and_size_checked(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        generated = next(item for item in catalog["fixtures"] if item["asset"]["kind"] == "generated")
        generated["asset"]["sha256"] = "sha256:" + "0" * 64
        errors = validate_catalog(catalog, root=ROOT)
        self.assertTrue(any("hash does not match" in error for error in errors))

    def test_malicious_path_and_bad_decimal_are_rejected(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        catalog["fixtures"][0]["asset"]["path"] = "../outside.mp4"
        errors = validate_catalog(catalog, root=None)
        self.assertTrue(any("safe repository-relative" in error for error in errors))
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        catalog["fixtures"][0]["timeline"]["duration_ticks"] = "01"
        errors = validate_catalog(catalog, root=None)
        self.assertTrue(any("bounded decimal" in error for error in errors))

    def test_external_asset_requires_fetch_metadata(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        external = next(item for item in catalog["fixtures"] if item["asset"]["kind"] == "external")
        external["asset"]["fetch_uri"] = None
        errors = validate_catalog(catalog, root=None)
        self.assertTrue(any("fetch_uri" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from packaging.manifests import (
    ManifestError,
    evaluate_default_eligibility,
    load_catalog,
    validate_catalog,
    verify_candidate,
)


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "models" / "manifests" / "catalog-v1.json"


class ManifestValidatorTests(unittest.TestCase):
    def test_reference_catalog_is_machine_readable_and_has_ffmpeg(self) -> None:
        entries = load_catalog(CATALOG)
        self.assertEqual({entry["kind"] for entry in entries}, {"model", "runtime", "ffmpeg"})
        self.assertTrue(all("fallback" in entry for entry in entries))
        self.assertTrue(all(evaluate_default_eligibility(entry).eligible for entry in entries if entry["default_profile"]))

    def test_credentials_clickthrough_and_remote_code_never_become_default(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        base = next(entry for entry in catalog["entries"] if entry["id"] == "asr-baseline")
        for field in ("requires_credentials", "requires_clickthrough"):
            candidate = {**base, field: True}
            self.assertFalse(evaluate_default_eligibility(candidate).eligible)
        candidate = {**base, "trust": {**base["trust"], "trust_remote_code": True}}
        self.assertFalse(evaluate_default_eligibility(candidate).eligible)

    def test_code_bearing_default_requires_signature_and_audit(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        entry = next(item for item in catalog["entries"] if item["id"] == "ffmpeg-runtime")
        candidate = {**entry, "trust": {**entry["trust"], "signed": False, "audit_id": None}}
        result = evaluate_default_eligibility(candidate)
        self.assertFalse(result.eligible)
        self.assertIn("not signed", " ".join(result.reasons))

    def test_decimal_u64_and_hash_size_verification_are_exact(self) -> None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        with self.assertRaises(ManifestError):
            validate_catalog({**catalog, "entries": [{**catalog["entries"][0], "size_bytes": "18446744073709551616"}]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"manifest fixture")
            entry = {**catalog["entries"][0], "size_bytes": str(path.stat().st_size), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertTrue(verify_candidate(path, entry))
            self.assertFalse(verify_candidate(path, {**entry, "size_bytes": "1"}))


if __name__ == "__main__":
    unittest.main()

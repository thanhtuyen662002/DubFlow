from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_review_export as fixtures
from engine.dubflow.capcut.import_pack import ImportPackError, validate_import_pack
from engine.dubflow.worker.review_editable_pack import _volume_id, build_candidate


class ReviewEditableWorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReviewBundleEditableTests()
        self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self):
        self.fixture.tearDown()

    def context(self, mode="b2", maximum=32 * 1024**3):
        bundle, manifest, _ = self.fixture.bundle(mode)
        storage = self.root / "native storage with spaces"
        storage.mkdir()
        job = sha256(manifest["current_token"]["job_id"].encode()).hexdigest()
        moved = storage / ".dubflow-review-media-v1" / job / ".exports-published-v1" / manifest["generation"]
        moved.parent.mkdir(parents=True)
        bundle.rename(moved)
        context = {"schema_version": 1, "kind": "review_native_editable_pack_context",
            "current_token": manifest["current_token"], "bundle_root": str(moved),
            "bundle_manifest_sha256": fixtures.hashed((moved / "review_export_manifest.json").read_bytes()),
            "generation": manifest["generation"], "storage_root": str(storage),
            "output_volume_id": _volume_id(storage),
            "processing_recipe_identity": fixtures.hashed(b"test-only source recipe"),
            "maximum_bytes": str(maximum), "minimum_free_bytes": str(2 * 1024**3)}
        return context, moved, storage

    def test_isolated_child_creates_and_recovers_same_candidate_without_input_or_pack_rewrite(self):
        context, bundle, _ = self.context()
        path = self.root / "native context.json"
        raw = fixtures.encoded(context)
        path.write_bytes(raw)
        path.chmod(stat.S_IREAD)
        worker = Path(__file__).resolve().parents[2] / "engine/dubflow/worker/review_editable_pack.py"
        argv = [sys.executable, "-I", "-S", "-B", str(worker), "--context", str(path), "--context-sha256", fixtures.hashed(raw)]
        before = {p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}
        first = subprocess.run(argv, input=b"\x01", capture_output=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, b"")
        result = json.loads(first.stdout)
        self.assertEqual(result["resource_count"], 10)
        self.assertFalse(result["current_generation_checked"])
        pack = Path(result["candidate_path"])
        checked = validate_import_pack(pack)
        self.assertEqual(checked["pack_id"], "review-" + context["generation"])
        legacy_bytes = self.root / "legacy manifest physical bytes.json"
        legacy_bytes.write_text(json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        self.assertEqual((pack / "manifest.json").read_bytes(), legacy_bytes.read_bytes())
        pins = {str(p.relative_to(pack)): (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in pack.rglob("*") if p.is_file()}
        second = subprocess.run(argv, input=b"\x01", capture_output=True, timeout=30)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout), result)
        self.assertEqual({str(p.relative_to(pack)): (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in pack.rglob("*") if p.is_file()}, pins)
        self.assertEqual({p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}, before)
        rejected = subprocess.run(argv, input=b"\x00", capture_output=True, timeout=30)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, b"")

    def test_quota_includes_published_input_and_refuses_before_candidate_or_source_mutation(self):
        context, bundle, storage = self.context("b1", maximum=1)
        raw = fixtures.encoded(context)
        before = {p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}
        with self.assertRaisesRegex(ImportPackError, "quota/fresh reserve"):
            build_candidate(raw, fixtures.hashed(raw))
        self.assertFalse(list(storage.rglob("import-pack")))
        self.assertEqual({p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}, before)

    def test_reserve_change_during_copy_refuses_and_resumes_with_changed_space_condition(self):
        context, _, storage = self.context()
        raw = fixtures.encoded(context)
        calls = 0

        def space(_):
            nonlocal calls
            calls += 1
            return 10**15 if calls < 6 else 0

        with patch("engine.dubflow.worker.review_editable_pack._available_bytes", side_effect=space):
            with self.assertRaisesRegex(ImportPackError, "quota/fresh reserve"):
                build_candidate(raw, fixtures.hashed(raw))
        self.assertFalse(list(storage.rglob("import-pack")))
        result = build_candidate(raw, fixtures.hashed(raw))
        self.assertTrue(Path(result["candidate_path"]).is_dir())
        self.assertEqual(result["resource_count"], 10)

    def test_native_volume_pin_change_refuses_without_source_mutation_or_promotion(self):
        context, bundle, storage = self.context()
        raw = fixtures.encoded(context)
        before = {p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}
        calls = 0

        def observed(_):
            nonlocal calls
            calls += 1
            return context["output_volume_id"] if calls < 5 else "windows-volume-guid:00000000-0000-0000-0000-000000000001"

        with patch("engine.dubflow.worker.review_editable_pack._volume_id", side_effect=observed):
            with self.assertRaisesRegex(ImportPackError, "volume changed"):
                build_candidate(raw, fixtures.hashed(raw))
        self.assertFalse(list(storage.rglob("import-pack")))
        self.assertEqual({p.name: (fixtures.hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in bundle.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()

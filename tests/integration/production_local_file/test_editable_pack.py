"""Publication/recovery regressions using explicit contract-fixture bytes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

fixture_path = Path(__file__).resolve().parents[2] / "capcut_import/test_export_assembly.py"
fixture_spec = importlib.util.spec_from_file_location("_canonical_pack_fixture", fixture_path)
fixtures = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixtures)
from engine.dubflow.capcut.import_pack import ImportPackError, validate_import_pack
from engine.dubflow.capcut.import_pack import adapter
from engine.dubflow.worker.editable_pack import assemble_export_pack, validate_export_pack
from engine.dubflow.worker import export_publication


class ProductionEditablePackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CanonicalPackAssemblyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.export = self.fixture.export
        self.work = self.fixture.root / "private worker cache"

    def assemble(self):
        result = assemble_export_pack(self.fixture.manifest, self.work)
        self.fixture.document["editable_pack"] = result
        manifest = self.export / result["relative_path"] / "manifest.json"
        self.fixture.artifacts["import_pack_manifest"] = {"path": str(manifest),
            "sha256": result["manifest_sha256"], "size_bytes": manifest.stat().st_size}
        self.fixture.save_manifest()
        return result

    def test_complete_b2_export_is_independently_validated_after_move(self):
        before = {name: item["sha256"] for name, item in self.fixture.artifacts.items()}
        descriptor = self.assemble()
        self.assertEqual(descriptor["state"], "available")
        moved = self.fixture.root / "moved complete export with spaces"
        self.export.rename(moved)
        result = validate_export_pack(moved, expected_job_id=self.fixture.document["job_id"])
        self.assertEqual(Path(result["path"]), (moved / "editable/import-pack").resolve())
        self.assertEqual(len(validate_import_pack(Path(result["path"]))["assets"]), 8)
        self.assertEqual(before, {name: self.fixture.artifacts[name]["sha256"] for name in before})

    def test_failed_copy_retains_private_checkpoint_and_never_publishes_partial_pack(self):
        original = adapter._copy_asset
        copied = []
        def interrupted(source, target, **kwargs):
            if copied:
                raise OSError("authored copy interruption")
            original(source, target, **kwargs)
            copied.append((target, target.stat().st_mtime_ns, target.read_bytes()))
        with patch.object(adapter, "_copy_asset", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.assemble()
        self.assertFalse((self.export / "editable/import-pack").exists())
        retained, mtime, payload = copied[0]
        self.assertTrue(retained.is_relative_to(self.work))
        self.assertEqual(retained.read_bytes(), payload)
        descriptor = self.assemble()
        pack = self.export / descriptor["relative_path"]
        asset = next(a for a in validate_import_pack(pack)["assets"] if a["asset_id"] == "source-video")
        restored = pack / asset["relative_path"]
        self.assertEqual(restored.read_bytes(), payload)
        self.assertEqual(restored.stat().st_mtime_ns, mtime)

    def test_promoted_candidate_recovery_reuses_verified_files_without_copying(self):
        descriptor = self.assemble()
        manifest = self.export / descriptor["relative_path"] / "manifest.json"
        before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
        with patch("engine.dubflow.worker.editable_pack.create_pack_from_export", side_effect=AssertionError("must reuse verified candidate")):
            self.assertEqual(self.assemble()["manifest_sha256"], descriptor["manifest_sha256"])
        self.assertEqual((manifest.read_bytes(), manifest.stat().st_mtime_ns), before)

    def test_isolated_read_only_cli_validates_actual_paths_and_refuses_wrong_job(self):
        self.assemble()
        script = Path(__file__).resolve().parents[3] / "engine/dubflow/worker/editable_pack.py"
        before = self.fixture.manifest.read_bytes()
        command = [sys.executable, "-I", "-S", "-B", str(script), "--export-dir", str(self.export), "--job-id"]
        for job, expected in ((self.fixture.document["job_id"], "available"), ("wrong-job", "unavailable")):
            result = subprocess.run(command + [job], capture_output=True, check=True, timeout=30)
            report = json.loads(result.stdout)
            self.assertEqual(report["state"], expected)
            if expected == "available":
                self.assertEqual(Path(report["path"]), (self.export / "editable/import-pack").resolve())
        self.assertEqual(self.fixture.manifest.read_bytes(), before)

    def test_changed_canonical_timeline_cannot_relabel_existing_pack(self):
        self.assemble()
        pack = self.export / "editable/import-pack"
        old = (pack / "manifest.json").read_bytes()
        self.fixture.timeline["cues"][0]["translated_text"] = "Changed translation"
        self.fixture.add_artifact("editable_timeline", "editable/timeline.json", json.dumps(self.fixture.timeline).encode())
        self.fixture.save_manifest()
        with self.assertRaisesRegex(ImportPackError, "resources differ"):
            self.assemble()
        self.assertEqual((pack / "manifest.json").read_bytes(), old)

    def test_corrupt_pack_and_wrong_job_are_refused_without_touching_canonical_video(self):
        descriptor = self.assemble()
        final = self.export / "final_vi.mp4"
        before = final.read_bytes()
        with self.assertRaisesRegex(ImportPackError, "another job"):
            validate_export_pack(self.export, expected_job_id="other-job")
        pack = self.export / descriptor["relative_path"]
        asset = validate_import_pack(pack)["assets"][0]
        (pack / asset["relative_path"]).write_bytes(b"corrupt")
        with self.assertRaises(ImportPackError):
            validate_export_pack(self.export)
        self.assertEqual(final.read_bytes(), before)

    def test_pack_survives_optional_discovery_failure(self):
        with patch("engine.dubflow.worker.editable_pack.discover_capcut_installation", side_effect=PermissionError("registry unavailable")):
            descriptor = self.assemble()
        self.assertEqual(descriptor["state"], "available")
        self.assertEqual(descriptor["capcut"]["discovery"]["status"], "INCOMPLETE")
        self.assertEqual(validate_export_pack(self.export)["state"], "available")

    def test_available_descriptor_requires_exact_schema_recipe_and_canonical_artifact_pin(self):
        descriptor = self.assemble()
        for altered in ({**descriptor, "schema_version": True}, {**descriptor, "recipe": "future-unsupported"}):
            self.fixture.document["editable_pack"] = altered
            self.fixture.save_manifest()
            with self.assertRaisesRegex(ImportPackError, "unsupported"):
                validate_export_pack(self.export)
        self.fixture.document["editable_pack"] = descriptor
        self.fixture.artifacts["import_pack_manifest"]["sha256"] = "sha256:" + "0" * 64
        self.fixture.save_manifest()
        with self.assertRaisesRegex(ImportPackError, "canonical artifact pin"):
            validate_export_pack(self.export)

    def test_b1_does_not_invent_missing_audio_and_legacy_export_stays_readable(self):
        self.assertEqual(validate_export_pack(self.export)["reason"], "LEGACY_EXPORT_WITHOUT_IMPORT_PACK")
        for name in ("editable_source_audio", "editable_dialogue_stem", "editable_final_mix"):
            self.fixture.artifacts.pop(name)
        self.fixture.save_manifest()
        self.assemble()
        manifest = validate_import_pack(self.export / "editable/import-pack")
        self.assertEqual(len(manifest["assets"]), 5)
        self.assertFalse(any(a["kind"].endswith("audio") for a in manifest["assets"]))

    def test_editable_pack_is_committed_and_rolled_back_with_canonical_export(self):
        self.assemble()
        output = self.fixture.root / "published export"
        pending = export_publication.prepare(output)
        for path in list(self.export.iterdir()):
            path.rename(pending / path.name)
        with patch.object(export_publication, "write_journal", wraps=export_publication.write_journal) as journal:
            export_publication.publish(output)
        self.assertTrue((output / "editable/import-pack/manifest.json").is_file())
        self.assertTrue(any(item["name"] == "editable" for item in journal.call_args_list[0].args[1]["items"]))
        old = (output / "final_vi.mp4").read_bytes()
        candidate = export_publication.prepare(output)
        (candidate / "final_vi.mp4").write_bytes(b"unpublished replacement")
        (candidate / "job_manifest.json").write_text("{}")
        real_replace = export_publication.os.replace
        def fail_once(source, target):
            if Path(source) == candidate / "job_manifest.json":
                raise OSError("authored publication failure")
            return real_replace(source, target)
        with patch.object(export_publication.os, "replace", side_effect=fail_once):
            with self.assertRaises(OSError):
                export_publication.publish(output)
        self.assertEqual((output / "final_vi.mp4").read_bytes(), old)
        self.assertEqual(validate_export_pack(output)["state"], "available")


if __name__ == "__main__":
    unittest.main()

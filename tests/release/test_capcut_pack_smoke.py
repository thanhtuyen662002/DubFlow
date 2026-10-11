"""Guards against accepting absent/stale/misdirected native pack evidence."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.release.production_smoke import SmokeError, _verify_import_packs


class NativePackQualificationTests(unittest.TestCase):
    def setUp(self):
        self.scratch = TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.output = self.root / "native output with spaces"
        self.pack = self.output / "editable/import-pack"
        self.pack.mkdir(parents=True)
        self.sha = "sha256:" + "a" * 64
        self.manifest = {"job_id": "native-job", "editable_pack": {"state": "available", "manifest_sha256": self.sha,
            "capcut": {"discovery": {"status": "NOT_REGISTERED"}}}}
        self.save()
        (self.pack / "manifest.json").write_text(json.dumps({"schema_version": 2, "assets": [1, 2, 3, 4, 5]}))
        self.report = {"schema_version": 1, "state": "available", "path": str(self.pack), "manifest_sha256": self.sha}
        self.output_report = {"output": str(self.output / "final_vi.mp4"), "video_codec": "h264"}

    def save(self):
        (self.output / "job_manifest.json").write_text(json.dumps(self.manifest))

    def verify(self, response=None):
        result = subprocess.CompletedProcess([], 0, json.dumps(self.report if response is None else response), "")
        with patch("scripts.release.production_smoke._run", return_value=result) as native:
            report = _verify_import_packs(self.root, [self.output_report, {"resume": self.output_report}], timeout=600)
        return report, native

    def test_native_validator_is_owned_isolated_source_bound_and_deduplicated(self):
        report, native = self.verify()
        self.assertEqual(len(report["cases"]), 1)
        self.assertEqual(report["cases"][0]["asset_count"], 5)
        command = native.call_args.args[0]
        self.assertEqual(command[0], str(self.root / "runtime/python.exe"))
        self.assertEqual(command[1:4], ["-I", "-S", "-B"])
        self.assertEqual(command[-2:], ["--job-id", "native-job"])

    def test_missing_worker_pack_cannot_pass_as_standard_output(self):
        self.manifest.pop("editable_pack");self.save()
        with self.assertRaisesRegex(SmokeError, "did not emit"):
            self.verify()

    def test_unavailable_native_report_cannot_be_counted_as_validated(self):
        with self.assertRaisesRegex(SmokeError, "did not verify"):
            self.verify({"schema_version": 1, "state": "unavailable", "reason": "corrupt"})

    def test_old_hash_or_wrong_path_cannot_substitute_for_current_pack(self):
        for altered in ({**self.report, "manifest_sha256": "sha256:" + "b" * 64},
                        {**self.report, "path": str(self.root / "other pack")}):
            with self.subTest(altered=altered), self.assertRaisesRegex(SmokeError, "did not verify"):
                self.verify(altered)

    def test_no_successful_worker_output_is_explicit_failure(self):
        with self.assertRaisesRegex(SmokeError, "no actual completed"):
            _verify_import_packs(self.root, {"failed": {}}, timeout=600)


if __name__ == "__main__":
    unittest.main()

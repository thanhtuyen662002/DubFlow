from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import wave
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.review import ReviewError, build_review_from_export, validate_review_preview
from engine.dubflow.review.export import read_review_preview_bytes
from schema_assertions import assert_schema

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_tts_test_helpers", ROOT / "tests/tts/test_adapter.py")
tts_helpers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tts_helpers
spec.loader.exec_module(tts_helpers)


def sha(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class ExportReviewTests(unittest.TestCase):
    def fixture(self, directory, *, tts=False):
        output = Path(directory) / "Output with spaces"
        output.mkdir()
        timeline = {"schema_version": 1, "time_base": {"numerator": 1, "denominator": 1000},
                    "duration_ticks": "1000", "cues": [{"cue_id": "cue-a", "start_ms": 0, "end_ms": 1000,
                      "source_text": "Hello", "translated_text": "Xin chào", "confidence": 0.1}]}
        qc = {"schema_version": 1, "status": "passed", "warnings": ["TTS_FAILURE: cue-a; check audio", "QC_WARNING: global advisory"]}
        values = {"editable_timeline": timeline, "qc_report": qc}
        if tts:
            engine = tts_helpers.DeterministicFixtureEngine()
            adapter = tts_helpers.adapter(engine, output_dir=output / ".dubflow-work/tts")
            document = adapter.synthesize((tts_helpers.segment("cue-a"),), input_hash=tts_helpers.INPUT_HASH)
            values["tts_document"] = document.to_dict()
        manifest = {"schema_version": 1, "kind": "dubflow_localization_job", "job_id": "job-review",
                    "dubbing": {"enabled": tts, "requested_voice_id": None}, "artifacts": {}}
        for role, value in values.items():
            path = output / (role + ".json")
            path.write_text(json.dumps(value), encoding="utf-8")
            manifest["artifacts"][role] = {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}
        path = output / "job_manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path, manifest

    def build(self, path):
        return build_review_from_export(path, expected_job_id="job-review", expected_manifest_sha256=sha(path))

    def update(self, manifest_path, manifest, role, value):
        path = Path(manifest["artifacts"][role]["path"])
        path.write_text(json.dumps(value), encoding="utf-8")
        manifest["artifacts"][role].update(sha256=sha(path), size_bytes=path.stat().st_size)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_b1_generic_review_preserves_unknown_voice_and_global_warning(self):
        with TemporaryDirectory(prefix="dubflow-review-export-") as directory:
            path, _ = self.fixture(directory)
            bound = self.build(path)
            self.assertIsNone(bound.tts_provenance_json)
            self.assertEqual(len(bound.source_pins), 3)
            self.assertTrue(all(item.voice is None and item.output_preview is None for item in bound.snapshot.findings))
            self.assertTrue(any(item.cue_id is None and item.start_ticks is None for item in bound.snapshot.findings))
            assert_schema(bound.snapshot.to_dict(), "schema-v2.json")

    def test_default_request_uses_actual_tts_provenance_and_verified_pcm_preview(self):
        with TemporaryDirectory(prefix="dubflow-review-default-") as directory:
            path, manifest = self.fixture(directory, tts=True)
            before = {role: sha(Path(item["path"])) for role, item in manifest["artifacts"].items()}
            bound = self.build(path)
            provenance = json.loads(bound.tts_provenance_json)
            affected = [item for item in bound.snapshot.findings if item.cue_id == "cue-a"]
            self.assertIsNone(manifest["dubbing"]["requested_voice_id"])
            self.assertTrue(all(item.voice == provenance["voice_id"] and item.output_preview is not None for item in affected))
            preview = validate_review_preview(bound, "cue-a")
            self.assertEqual(str(preview), affected[0].output_preview)
            self.assertEqual(before, {role: sha(Path(item["path"])) for role, item in manifest["artifacts"].items()})

    def test_metadata_hash_and_job_mismatch_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-pins-") as directory:
            path, manifest = self.fixture(directory)
            with self.assertRaisesRegex(ReviewError, "hash changed"):
                build_review_from_export(path, expected_job_id="job-review", expected_manifest_sha256="sha256:" + "0" * 64)
            with self.assertRaisesRegex(ReviewError, "job"):
                build_review_from_export(path, expected_job_id="other-job", expected_manifest_sha256=sha(path))
            timeline = Path(manifest["artifacts"]["editable_timeline"]["path"])
            timeline.write_bytes(timeline.read_bytes() + b" ")
            with self.assertRaisesRegex(ReviewError, "size changed|hash changed"):
                self.build(path)

    def test_parent_escape_and_failed_qc_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-paths-") as directory:
            path, manifest = self.fixture(directory)
            outside = Path(directory) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            manifest["artifacts"]["editable_timeline"] = {"path": str(path.parent / ".." / "outside.json"),
                                                        "sha256": sha(outside), "size_bytes": outside.stat().st_size}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ReviewError, "outside"):
                self.build(path)
        with TemporaryDirectory(prefix="dubflow-review-qc-") as directory:
            path, manifest = self.fixture(directory)
            self.update(path, manifest, "qc_report", {"schema_version": 1, "status": "failed", "warnings": []})
            with self.assertRaisesRegex(ReviewError, "not passed"):
                self.build(path)

    def test_missing_one_preview_is_bound_review_data_and_preserves_output(self):
        with TemporaryDirectory(prefix="dubflow-review-missing-") as directory:
            path, manifest = self.fixture(directory, tts=True)
            tts = json.loads(Path(manifest["artifacts"]["tts_document"]["path"]).read_text(encoding="utf-8"))
            Path(tts["artifacts"][0]["path"]).unlink()
            before = path.read_bytes()
            bound = self.build(path)
            self.assertTrue(any(item.warning.startswith("TTS_PREVIEW_UNAVAILABLE:") and item.cue_id == "cue-a" for item in bound.snapshot.findings if item.warning))
            self.assertTrue(all(item.output_preview is None for item in bound.snapshot.findings))
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ReviewError, "no validated"):
                validate_review_preview(bound, "cue-a")

    def test_corrupt_pcm_and_preview_bound_refuse_before_playback(self):
        with TemporaryDirectory(prefix="dubflow-review-preview-") as directory:
            path, _ = self.fixture(directory, tts=True)
            bound = self.build(path)
            preview = validate_review_preview(bound, "cue-a")
            with patch("engine.dubflow.review.export.MAX_PREVIEW_BYTES", 1), self.assertRaisesRegex(ReviewError, "bounded"):
                validate_review_preview(bound, "cue-a")
            raw = bytearray(preview.read_bytes())
            raw[-1] ^= 1
            preview.write_bytes(raw)
            with self.assertRaisesRegex(ReviewError, "WAV content changed"):
                validate_review_preview(bound, "cue-a")

    def test_audio_delivery_uses_the_same_hash_verified_buffer_after_path_changes(self):
        with TemporaryDirectory(prefix="dubflow-review-delivery-") as directory:
            path, _ = self.fixture(directory, tts=True)
            bound = self.build(path)
            preview = validate_review_preview(bound, "cue-a")
            original = preview.read_bytes()
            open_wave = wave.open

            def change_path_after_read(buffer, mode):
                altered = bytearray(original); altered[-1] ^= 1
                preview.write_bytes(altered)
                return open_wave(buffer, mode)

            with patch("engine.dubflow.review.export.wave.open", side_effect=change_path_after_read):
                self.assertEqual(read_review_preview_bytes(bound, "cue-a"), original)
            with self.assertRaisesRegex(ReviewError, "WAV content changed"):
                read_review_preview_bytes(bound, "cue-a")

    def test_unbounded_or_outside_canonical_cues_refuse(self):
        with TemporaryDirectory(prefix="dubflow-review-range-") as directory:
            path, manifest = self.fixture(directory)
            timeline = json.loads(Path(manifest["artifacts"]["editable_timeline"]["path"]).read_text(encoding="utf-8"))
            timeline["cues"][0]["end_ms"] = 1001
            self.update(path, manifest, "editable_timeline", timeline)
            with self.assertRaisesRegex(ReviewError, "outside source"):
                self.build(path)
            with patch("engine.dubflow.review.export.MAX_JSON_BYTES", 1), self.assertRaisesRegex(ReviewError, "JSON byte limit"):
                self.build(path)


if __name__ == "__main__":
    unittest.main()

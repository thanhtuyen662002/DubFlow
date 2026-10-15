from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from engine.dubflow.capcut import create_pack_from_review_bundle
from engine.dubflow.capcut.import_pack import ImportPackError, validate_import_pack


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def hashed(raw):
    return "sha256:" + sha256(raw).hexdigest()


def writable(path):
    path.chmod(stat.S_IREAD | stat.S_IWRITE)


class ReviewBundleEditableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dubflow-review-editable-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        # This fixture owns every file under its fresh unique directory only.
        for path in self.root.rglob("*"):
            if path.is_file():
                writable(path)
        self.temporary.cleanup()

    def bundle(self, mode="b2"):
        root = self.root / ("Review bundle " + mode)
        root.mkdir()
        # Byte-only fixtures exercise full resource/copy/tick binding. They do
        # not establish encoded video, synthesis quality or installed acceptance.
        timeline = {"schema_version": 1, "time_base": {"numerator": 1, "denominator": 90000},
                    "duration_ticks": 360000,
                    "source_clock_mapping": {"recipe": "source-video-origin-v1", "start_pts": "9000"},
                    "cues": [{"cue_id": "cue-1", "start_ms": 1001, "end_ms": 2002,
                              "source_text": "Hello", "translated_text": "Một người tôi rất quý.",
                              "confidence": 0.9838564395904541}]}
        snapshot = {"schema_version": 2, "job_id": "review-job", "revision": 9007199254740993,
                    "time_base": {"numerator": "1", "denominator": "90000"}, "findings": [], "last_plan": None}
        snapshot_raw = encoded(snapshot).decode("utf-8")
        current = {"job_id": "review-job", "revision": "9007199254740993", "snapshot_sha256": hashed(snapshot_raw.encode()),
                   "graph_sha256": hashed(b"test-only corrected graph"), "original_manifest_sha256": hashed(b"original manifest")}
        origin = dict(current, revision="0", snapshot_sha256=hashed(b"original snapshot"), graph_sha256=hashed(b"original graph"))
        resources = [("final_video", "final_vi.mp4", b"fixture localized video"),
                     ("captions_srt", "captions_vi.srt", "1\n00:00:01,001 --> 00:00:02,002\nMột người tôi rất quý.\n".encode()),
                     ("captions_ass", "captions_vi.ass", b"fixture ASS"),
                     ("corrected_timeline", "timeline.json", encoded(timeline)),
                     ("qc_report", "qc_report.json", encoded({"schema_version": 1, "status": "passed"})),
                     ("source_video", "source.mp4", b"fixture raw source")]
        if mode == "b2":
            resources += [("original_audio", "original_audio.wav", b"fixture original audio"),
                          ("dialogue_stem", "dialogue_stem.wav", b"fixture dialogue stem"),
                          ("final_mix", "final_mix.wav", b"fixture final mixed audio")]
        files = []
        for role, name, raw in resources:
            path = root / name
            path.write_bytes(raw)
            path.chmod(stat.S_IREAD)
            files.append({"role": role, "relative_path": name, "sha256": hashed(raw), "size_bytes": str(len(raw))})
        manifest = {"schema_version": 1, "kind": "review_corrected_standard_bundle", "recipe": "review-corrected-standard-bundle-v1",
                    "generation": "", "current_token": current, "origin_token": origin, "mode": mode,
                    "current_snapshot_json": snapshot_raw, "publication_sha256": hashed(b"native test publication"),
                    "retained_render_sha256": hashed(b"native test retained receipt"), "files": files}
        self.write_manifest(root, manifest)
        return root, manifest, timeline

    def write_manifest(self, root, manifest):
        manifest["generation"] = sha256(encoded({name: value for name, value in manifest.items() if name != "generation"})).hexdigest()
        path = root / "review_export_manifest.json"
        if path.exists():
            writable(path)
        raw = encoded(manifest)
        path.write_bytes(raw)
        path.chmod(stat.S_IREAD)
        return hashed(raw)

    def run_pack(self, root, manifest, output="editable pack"):
        return create_pack_from_review_bundle(root, self.root / output,
            expected_manifest_hash=hashed((root / "review_export_manifest.json").read_bytes()),
            expected_current_token=manifest["current_token"])

    def test_b2_moved_bundle_full_resource_binding_exact_ticks_and_tracks(self):
        root, manifest, timeline = self.bundle()
        before = {path.name: (hashed(path.read_bytes()), path.stat().st_mtime_ns) for path in root.iterdir()}
        moved = self.root / "moved Review bundle with spaces"
        root.rename(moved)
        result = self.run_pack(moved, manifest)
        self.assertIsNone(result.production.direct_draft)
        pack = result.production.pack.root
        checked = validate_import_pack(pack)
        self.assertEqual(len(checked["assets"]), 10)
        assets = {asset["asset_id"]: asset for asset in checked["assets"]}
        self.assertEqual((pack / assets["canonical-timeline"]["relative_path"]).read_bytes(), encoded(timeline))
        self.assertEqual(checked["timeline"]["time_base"], {"numerator": "1", "denominator": "90000"})
        self.assertEqual(checked["timeline"]["cues"][0]["start_ticks"], "90090")
        self.assertEqual(checked["timeline"]["cues"][0]["end_ticks"], "180180")
        active_audio = [t for t in checked["tracks"] if t["kind"] == "audio" and t["enabled_by_default"]]
        self.assertEqual([t["role"] for t in active_audio], ["mixed_audio"])
        self.assertTrue(next(t for t in checked["tracks"] if t["kind"] == "video" and t["enabled_by_default"])["mute_embedded_audio"])
        self.assertEqual(result.to_dict()["review_binding"]["current_token"]["revision"], "9007199254740993")
        self.assertFalse(result.to_dict()["review_binding"]["current_generation_checked"])
        pack.rename(self.root / "moved editable pack")
        self.assertEqual(validate_import_pack(self.root / "moved editable pack"), checked)
        self.assertEqual({p.name: (hashed(p.read_bytes()), p.stat().st_mtime_ns) for p in moved.iterdir()}, before)

    def test_b1_keeps_source_audio_and_does_not_invent_pcm_or_mix(self):
        root, manifest, _ = self.bundle("b1")
        checked = self.run_pack(root, manifest).production.pack.manifest
        self.assertEqual(len(checked["assets"]), 7)
        self.assertFalse(any(t["kind"] == "audio" for t in checked["tracks"]))
        self.assertFalse(next(t for t in checked["tracks"] if t["kind"] == "video" and t["enabled_by_default"])["mute_embedded_audio"])

    def test_wrong_token_equal_size_corruption_and_extra_file_refuse_before_output(self):
        root, manifest, _ = self.bundle()
        out = self.root / "refused pack"
        expected = dict(manifest["current_token"], revision="9007199254740994")
        pin = hashed((root / "review_export_manifest.json").read_bytes())
        with self.assertRaises(ImportPackError):
            create_pack_from_review_bundle(root, out, expected_manifest_hash=pin, expected_current_token=expected)
        extra = root / "crash.partial"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(ImportPackError):
            create_pack_from_review_bundle(root, out, expected_manifest_hash=pin, expected_current_token=manifest["current_token"])
        extra.unlink()
        path = root / "final_mix.wav"
        writable(path)
        data = bytearray(path.read_bytes())
        data[2] ^= 1
        path.write_bytes(data)
        path.chmod(stat.S_IREAD)
        with self.assertRaises(ImportPackError):
            create_pack_from_review_bundle(root, out, expected_manifest_hash=pin, expected_current_token=manifest["current_token"])
        self.assertFalse(out.exists())

    def test_role_traversal_snapshot_mismatch_and_duplicate_manifest_refuse(self):
        root, manifest, _ = self.bundle()
        for change in range(3):
            value = deepcopy(manifest)
            if change == 0:
                value["files"][0]["relative_path"] = "../foreign.mp4"
            elif change == 1:
                value["files"][0]["role"] = "source_video"
            else:
                value["current_snapshot_json"] += " "
            pin = self.write_manifest(root, value)
            with self.assertRaises(ImportPackError):
                create_pack_from_review_bundle(root, self.root / "invalid", expected_manifest_hash=pin, expected_current_token=value["current_token"])
        self.write_manifest(root, manifest)
        path = root / "review_export_manifest.json"
        raw = path.read_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
        writable(path)
        path.write_bytes(raw)
        path.chmod(stat.S_IREAD)
        with self.assertRaises(ImportPackError):
            create_pack_from_review_bundle(root, self.root / "invalid", expected_manifest_hash=hashed(raw), expected_current_token=manifest["current_token"])

    def test_output_overlap_and_hardlinked_bundle_resource_are_refused(self):
        root, manifest, _ = self.bundle()
        pin = hashed((root / "review_export_manifest.json").read_bytes())
        for out in (root, root / "new pack", root.parent):
            with self.assertRaises(ImportPackError):
                create_pack_from_review_bundle(root, out, expected_manifest_hash=pin, expected_current_token=manifest["current_token"])
        source = root / "source.mp4"
        link = self.root / "source alias.mp4"
        os.link(source, link)
        with self.assertRaises(ImportPackError):
            self.run_pack(root, manifest)
        # The fixture's owned teardown unseals both new hardlink names before
        # removing them on Windows; production input permissions stay unchanged.


if __name__ == "__main__":
    unittest.main()

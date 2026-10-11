from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.dubflow.capcut.draft.adapter import FixtureDraftBackend
from engine.dubflow.capcut.import_pack import ImportPackError, ImportPackRequest, create_import_pack, validate_import_pack
from engine.dubflow.capcut.production import create_pack_from_export


class CanonicalPackAssemblyTests(unittest.TestCase):
    """Deterministic file/contract tests; fixture bytes are not real media QA."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="dubflow-pack-assembly-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.export = self.root / "canonical export"
        self.export.mkdir()
        self.source = self.root / "raw input.mp4"
        self.source.write_bytes(b"raw source contract fixture")
        self.artifacts = {}
        for name, relative, content in (
            ("final_video", "final_vi.mp4", b"localized video contract fixture"),
            ("captions_srt", "captions_vi.srt", b"SRT caption bytes"),
            ("captions_ass", "captions_vi.ass", b"ASS caption bytes"),
            ("qc_report", "qc_report.json", b'{"schema_version":1,"status":"passed"}'),
            ("editable_source_audio", "editable/source_audio.wav", b"original stereo fixture"),
            ("editable_dialogue_stem", "editable/dialogue_stem.wav", b"dialogue fixture"),
            ("editable_final_mix", "editable/final_mix.wav", b"already combined mix fixture"),
        ):
            self.add_artifact(name, relative, content)
        self.timeline = {"schema_version": 1, "time_base": {"numerator": 1, "denominator": 1000},
                         "duration_ticks": 2000, "cues": [{"cue_id": "cue-1", "start_ms": 0, "end_ms": 1000,
                         "source_text": "Original", "translated_text": "Bản dịch", "confidence": 0.75}],
                         "source_clock_mapping": {"video_origin": {"ticks": "1024", "time_base": {"numerator": "1", "denominator": "12288"}}}}
        self.add_artifact("editable_timeline", "editable/timeline.json", json.dumps(self.timeline).encode())
        self.document = {"schema_version": 1, "kind": "dubflow_localization_job", "job_id": "assembly-172",
                         "source": {"path": str(self.source), "sha256": self.digest(self.source)}, "artifacts": self.artifacts}
        self.manifest = self.export / "job_manifest.json"
        self.save_manifest()

    def digest(self, path):
        return "sha256:" + sha256(path.read_bytes()).hexdigest()

    def add_artifact(self, name, relative, content):
        path = self.export / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.artifacts[name] = {"path": str(path), "sha256": self.digest(path), "size_bytes": len(content)}

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.document), encoding="utf-8")

    def test_b2_all_assets_move_with_exact_timeline_and_one_default_mix(self) -> None:
        before = {p: p.read_bytes() for p in self.export.rglob("*") if p.is_file()}
        result = create_pack_from_export(self.manifest, self.root / "pack", expected_manifest_hash=self.digest(self.manifest))
        moved = self.root / "moved editable folder"
        result.pack.root.rename(moved)
        manifest = validate_import_pack(moved)
        self.assertEqual(len(manifest["assets"]), 8)
        self.assertEqual({a["format"] for a in manifest["assets"] if a["kind"] == "subtitle"}, {"srt", "ass"})
        timeline = next(a for a in manifest["assets"] if a["kind"] == "timeline")
        self.assertEqual((moved / timeline["relative_path"]).read_bytes(), before[self.export / "editable/timeline.json"])
        self.assertEqual(manifest["timeline"]["cues"][0]["end_ticks"], "1000")
        enabled_audio = [t for t in manifest["tracks"] if t["kind"] == "audio" and t["enabled_by_default"]]
        self.assertEqual([t["role"] for t in enabled_audio], ["mixed_audio"])
        enabled_video = [t for t in manifest["tracks"] if t["kind"] == "video" and t["enabled_by_default"]]
        self.assertEqual(enabled_video[0]["asset_ids"], ["source-video"])
        self.assertTrue(enabled_video[0]["mute_embedded_audio"])
        self.assertEqual(sum(t["enabled_by_default"] for t in manifest["tracks"] if t["kind"] == "subtitle"), 1)
        self.assertEqual([t["order"] for t in manifest["tracks"]], list(range(len(manifest["tracks"]))))
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_b1_missing_stems_are_omitted_and_embedded_audio_remains(self) -> None:
        for name in ("editable_source_audio", "editable_dialogue_stem", "editable_final_mix"):
            self.artifacts.pop(name)
        self.save_manifest()
        manifest = create_pack_from_export(self.manifest, self.root / "pack").pack.manifest
        self.assertEqual(len(manifest["assets"]), 5)
        self.assertFalse(any(t["kind"] == "audio" for t in manifest["tracks"]))
        self.assertFalse(next(t for t in manifest["tracks"] if t["asset_ids"] == ["source-video"])["mute_embedded_audio"])

    def test_legacy_copied_stems_bind_private_descriptor_hashes(self) -> None:
        for name in ("source_audio", "dialogue_stem", "final_mix"):
            descriptor = self.artifacts.pop("editable_" + name)
            # Older exports list private paths but contain verified editable
            # copies. The portable adapter must use those contained copies.
            self.artifacts[name] = dict(descriptor, path=str(self.root / "missing private" / name))
        self.save_manifest()
        self.assertEqual(len(create_pack_from_export(self.manifest, self.root / "pack").pack.manifest["assets"]), 8)

    @unittest.skipUnless(os.name == "nt", "Windows native path spelling")
    def test_native_long_path_descriptors_bind_normal_root_spelling(self) -> None:
        for descriptor in self.artifacts.values():
            descriptor["path"] = "\\\\?\\" + descriptor["path"]
        self.save_manifest()
        result = create_pack_from_export(self.manifest, self.root / "pack")
        self.assertEqual(len(validate_import_pack(result.pack.root)["assets"]), 8)

    def test_changed_manifest_or_asset_fails_without_touching_standard_export(self) -> None:
        with self.assertRaisesRegex(ImportPackError, "caller's pin"):
            create_pack_from_export(self.manifest, self.root / "pack", expected_manifest_hash="sha256:" + "0" * 64)
        self.assertFalse((self.root / "pack").exists())
        changed = self.export / "editable/dialogue_stem.wav"
        changed.write_bytes(b"X" * changed.stat().st_size)
        video_before = (self.export / "final_vi.mp4").read_bytes()
        with self.assertRaisesRegex(ImportPackError, "hash differs"):
            create_pack_from_export(self.manifest, self.root / "pack")
        self.assertFalse((self.root / "pack").exists())
        self.assertEqual((self.export / "final_vi.mp4").read_bytes(), video_before)

    def test_direct_handoff_requires_explicit_user_selected_directory(self) -> None:
        result = create_pack_from_export(self.manifest, self.root / "pack", installed_capcut_version="4.2.0",
                                         supported_capcut_versions={"4.2.0"}, draft_backend=FixtureDraftBackend())
        self.assertEqual(result.direct_draft.reason, "CAPCUT_CONTROLLED_DIRECTORY_REQUIRED")
        self.assertFalse((self.root / "pack/capcut-controlled").exists())
        self.assertEqual(validate_import_pack(result.pack.root), result.pack.manifest)

    def test_reader_rejects_enabling_mix_and_component_together(self) -> None:
        pack = create_pack_from_export(self.manifest, self.root / "pack").pack
        for track in pack.manifest["tracks"]:
            if track["role"] == "dub_audio":
                track["enabled_by_default"] = True
        (pack.root / "manifest.json").write_text(json.dumps(pack.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ImportPackError, "enabled together"):
            validate_import_pack(pack.root)


class PackCopyRecoveryTests(unittest.TestCase):
    def test_interrupted_copy_resumes_proven_prefix_and_recovers_bad_private_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"0123456789abcdefghij")
            output = root / "pack"
            request = ImportPackRequest("copy-172", source, source_content_hash="sha256:" + sha256(source.read_bytes()).hexdigest())
            original_open = Path.open

            class InterruptedReader:
                def __init__(self, stream):
                    self.stream, self.reads = stream, 0
                def __enter__(self): return self
                def __exit__(self, *args): self.stream.close()
                def read(self, size):
                    self.reads += 1
                    if self.reads == 3:
                        raise OSError("controlled copy interruption")
                    return self.stream.read(size)

            def interrupted_open(path, mode="r", *args, **kwargs):
                stream = original_open(path, mode, *args, **kwargs)
                return InterruptedReader(stream) if path == source and mode == "rb" else stream

            with patch("engine.dubflow.capcut.import_pack.adapter._COPY_BYTES", 4), patch.object(Path, "open", interrupted_open):
                with self.assertRaisesRegex(OSError, "controlled copy interruption"):
                    create_import_pack(request, output)
            self.assertFalse(output.exists())
            checkpoints = list(root.glob(".pack.*.partial"))
            self.assertEqual(len(checkpoints), 1)
            retained = checkpoints[0] / "media/source.mp4"
            self.assertEqual(retained.read_bytes(), source.read_bytes()[:8])
            with retained.open("r+b") as stream:
                stream.seek(5)
                stream.write(b"XXX")
            with patch("engine.dubflow.capcut.import_pack.adapter._COPY_BYTES", 4):
                pack = create_import_pack(request, output)
            self.assertEqual((pack.root / "media/source.mp4").read_bytes(), source.read_bytes())
            self.assertFalse(checkpoints[0].exists())
            self.assertEqual(validate_import_pack(output), pack.manifest)

    def test_unrecognized_checkpoint_is_retained_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            request = ImportPackRequest("copy-172", source)
            with patch("engine.dubflow.capcut.import_pack.adapter._copy_asset", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    create_import_pack(request, root / "pack")
            marker = next(root.glob(".pack.*.partial")) / ".dubflow-input-pins.json"
            marker.write_bytes(b"unrecognized checkpoint")
            with self.assertRaisesRegex(ImportPackError, "unrecognized"):
                create_import_pack(request, root / "pack")
            self.assertEqual(marker.read_bytes(), b"unrecognized checkpoint")
            self.assertFalse((root / "pack").exists())


if __name__ == "__main__":
    unittest.main()

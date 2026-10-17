from __future__ import annotations

from pathlib import Path
import json
import re
import tempfile
import unittest

from engine.dubflow.capcut.draft import DraftStatus, DirectDraftError, FixtureDraftBackend, VersionedDraftAdapter
from engine.dubflow.capcut.draft.adapter import CapCutVersion, _validate_draft, _MAX_ENVELOPE_BYTES
from engine.dubflow.capcut.import_pack import ImportPackRequest, create_import_pack


class DraftAdapterTests(unittest.TestCase):
    def make_pack(self, root: Path) -> Path:
        source = root / "source.mp4"
        source.write_bytes(b"video")
        return create_import_pack(ImportPackRequest("job", source), root / "pack").root

    def test_exact_supported_version_generates_validated_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=pack, controlled_root=root / "controlled", backend=FixtureDraftBackend())
            self.assertEqual(result.status, DraftStatus.DIRECT)
            self.assertTrue(result.path and result.path.is_file())

    def test_unknown_version_falls_back_without_writing_capcut_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="5.0.0", pack_root=pack, controlled_root=root / "controlled", backend=FixtureDraftBackend())
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertEqual(result.reason, "CAPCUT_VERSION_UNSUPPORTED")
            self.assertFalse((root / "controlled").exists())

    def test_duplicate_envelope_fields_fall_back_and_preserve_prior_output(self) -> None:
        class DuplicateBackend(FixtureDraftBackend):
            def generate(self, **kwargs):
                super().generate(**kwargs)
                path = kwargs["output_path"]
                raw = path.read_text(encoding="utf-8")
                path.write_text(raw.replace('"schema_version": 2', '"schema_version": 1, "schema_version": 2'), encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pack = self.make_pack(root)
            adapter = VersionedDraftAdapter({"9.5.0.4050"})
            previous = adapter.generate_or_fallback(installed_version="9.5.0.4050", pack_root=pack,
                controlled_root=root / "controlled", backend=FixtureDraftBackend())
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = adapter.generate_or_fallback(installed_version="9.5.0.4050", pack_root=pack,
                controlled_root=root / "controlled", backend=DuplicateBackend())
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertIsNone(result.path)
            self.assertIn("not valid JSON", result.reason)
            self.assertEqual({p: p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)
            self.assertTrue(previous.path.is_file())

    def test_oversized_valid_json_is_refused_before_promotion(self) -> None:
        class OversizedBackend(FixtureDraftBackend):
            def generate(self, **kwargs):
                super().generate(**kwargs)
                path = kwargs["output_path"]
                remaining = _MAX_ENVELOPE_BYTES + 1 - path.stat().st_size
                with path.open("ab") as stream:
                    while remaining:
                        count = min(65536, remaining)
                        stream.write(b" " * count); remaining -= count

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pack = self.make_pack(root)
            before = {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()}
            result = VersionedDraftAdapter({"9.5.0.4050"}).generate_or_fallback(
                installed_version="9.5.0.4050", pack_root=pack,
                controlled_root=root / "controlled", backend=OversizedBackend())
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertIn("byte limit", result.reason)
            self.assertEqual(list((root / "controlled").iterdir()), [])
            self.assertEqual({p: p.read_bytes() for p in pack.rglob("*") if p.is_file()}, before)

    def test_nonregular_envelopes_do_not_authorize_direct_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pack = self.make_pack(root)
            version = CapCutVersion.parse("9.5.0.4050")
            with self.assertRaisesRegex(DirectDraftError, "plain regular"):
                _validate_draft(root, version=version, pack_root=pack)

    def test_linked_envelopes_do_not_authorize_direct_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pack = self.make_pack(root)
            version = CapCutVersion.parse("9.5.0.4050")
            valid = root / "valid.json"
            FixtureDraftBackend().generate(version=version, pack_root=pack, output_path=valid)
            before = valid.read_bytes()
            linked = root / "linked.json"
            try:
                linked.symlink_to(valid)
            except OSError as error:
                self.skipTest("Windows symlink privilege unavailable: " + str(error.errno))
            with self.assertRaisesRegex(DirectDraftError, "plain regular"):
                _validate_draft(linked, version=version, pack_root=pack)
            self.assertEqual(valid.read_bytes(), before)

    def test_four_component_build_identity_is_preserved(self) -> None:
        # Fixture admission checks version transport only, not vendor compatibility.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            version = "9.5.0.4050"
            result = VersionedDraftAdapter({version}).generate_or_fallback(
                installed_version=version, pack_root=pack,
                controlled_root=root / "controlled with spaces", backend=FixtureDraftBackend(),
            )
            self.assertEqual(result.status, DraftStatus.DIRECT)
            self.assertEqual(result.path.name, "dubflow-9.5.0.4050.draft.json")
            self.assertEqual(json.loads(result.path.read_text(encoding="utf-8"))["capcut_version"], version)

    def test_buildless_and_neighbor_builds_cannot_authorize_each_other(self) -> None:
        for supported, installed in (
            ("9.5.0", "9.5.0.4050"),
            ("9.5.0.4050", "9.5.0"),
            ("9.5.0.4050", "9.5.0.4051"),
        ):
            with self.subTest(supported=supported, installed=installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pack = self.make_pack(root)
                result = VersionedDraftAdapter({supported}).generate_or_fallback(
                    installed_version=installed, pack_root=pack,
                    controlled_root=root / "controlled", backend=FixtureDraftBackend(),
                )
                self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
                self.assertEqual(result.reason, "CAPCUT_VERSION_UNSUPPORTED")
                self.assertFalse((root / "controlled").exists())

    def test_backend_cannot_truncate_tested_build_in_output(self) -> None:
        class TruncatingBackend(FixtureDraftBackend):
            def generate(self, *, version, pack_root, output_path):
                super().generate(version=CapCutVersion.parse("9.5.0"), pack_root=pack_root, output_path=output_path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            result = VersionedDraftAdapter({"9.5.0.4050"}).generate_or_fallback(
                installed_version="9.5.0.4050", pack_root=pack,
                controlled_root=root / "controlled", backend=TruncatingBackend(),
            )
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertIn("tested version", result.reason)
            self.assertEqual(list((root / "controlled").iterdir()), [])

    def test_emitted_envelopes_match_published_version_contracts_and_retain_v1(self) -> None:
        contracts = Path(__file__).resolve().parents[2] / "contracts/capcut/draft"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            output = root / "draft.json"
            for version in ("4.2.0", "9.5.0.4050"):
                parsed = CapCutVersion.parse(version)
                FixtureDraftBackend().generate(version=parsed, pack_root=pack, output_path=output)
                value = json.loads(output.read_text(encoding="utf-8"))
                schema = json.loads((contracts / "schema-v2.json").read_text(encoding="utf-8"))
                self.assertEqual(value["schema_version"], schema["properties"]["schema_version"]["const"])
                self.assertEqual(set(value), set(schema["required"]))
                self.assertIsNotNone(re.fullmatch(schema["properties"]["capcut_version"]["pattern"], value["capcut_version"]))
                _validate_draft(output, version=parsed, pack_root=pack)
                # Old v1 remains readable only for its published version shape.
                value["schema_version"] = 1
                output.write_text(json.dumps(value), encoding="utf-8")
                before = output.read_bytes()
                legacy = json.loads((contracts / "schema-v1.json").read_text(encoding="utf-8"))
                if re.fullmatch(legacy["properties"]["capcut_version"]["pattern"], version):
                    _validate_draft(output, version=parsed, pack_root=pack)
                else:
                    with self.assertRaisesRegex(DirectDraftError, "v1 cannot represent"):
                        _validate_draft(output, version=parsed, pack_root=pack)
                self.assertEqual(output.read_bytes(), before)

    def test_backend_schema_or_asset_substitution_refuses_with_pack_preserved(self) -> None:
        class MutatingBackend(FixtureDraftBackend):
            def generate(self, *, version, pack_root, output_path):
                super().generate(version=version, pack_root=pack_root, output_path=output_path)
                value = json.loads(output_path.read_text(encoding="utf-8"))
                if case == "unknown":
                    value["schema_version"] = 3
                elif case == "boolean":
                    value["schema_version"] = True
                elif case == "extra":
                    value["unadmitted"] = "field"
                elif case == "asset":
                    value["assets"] = ["media/foreign.mp4"]
                else:
                    value["assets"] *= 2
                output_path.write_text(json.dumps(value), encoding="utf-8")

        for case in ("unknown", "boolean", "extra", "asset", "duplicate"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pack = self.make_pack(root)
                before = {path.relative_to(pack): path.read_bytes() for path in pack.rglob("*") if path.is_file()}
                result = VersionedDraftAdapter({"9.5.0.4050"}).generate_or_fallback(
                    installed_version="9.5.0.4050", pack_root=pack,
                    controlled_root=root / "controlled", backend=MutatingBackend(),
                )
                self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
                self.assertEqual(list((root / "controlled").iterdir()), [])
                self.assertEqual(before, {path.relative_to(pack): path.read_bytes() for path in pack.rglob("*") if path.is_file()})

    def test_version_comparison_retains_build_identity_and_legacy_ordering(self) -> None:
        versions = [CapCutVersion.parse(v) for v in ("9.5.0.4051", "9.5.0", "9.5.0.0", "9.5.0.4050", "4.2.0")]
        self.assertEqual(len(set(versions)), 5)
        self.assertEqual([str(v) for v in sorted(versions)], ["4.2.0", "9.5.0", "9.5.0.0", "9.5.0.4050", "9.5.0.4051"])
        for value in ("9.5", "9.5.0.4050.1", "9.5.0.04050", "9.5.0.-1", "9.5.0.4050\n", "9.5.0." + "1" * 11):
            with self.subTest(value=value), self.assertRaises(DirectDraftError):
                CapCutVersion.parse(value)

    def test_backend_failure_falls_back_and_cleans_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = self.make_pack(root)
            controlled = root / "controlled"
            result = VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=pack, controlled_root=controlled, backend=FixtureDraftBackend(fail=True))
            self.assertEqual(result.status, DraftStatus.FALLBACK_IMPORT_PACK)
            self.assertEqual(list(controlled.glob("*.partial")), [])

    def test_missing_pack_is_a_scoped_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DirectDraftError, "validated import pack"):
                VersionedDraftAdapter({"4.2.0"}).generate_or_fallback(installed_version="4.2.0", pack_root=Path(directory) / "missing", controlled_root=Path(directory) / "controlled", backend=FixtureDraftBackend())


if __name__ == "__main__":
    unittest.main()

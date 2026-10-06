from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from engine.dubflow.translation import argos_runtime as runtime
from engine.dubflow.translation.adapter import TranslationError


ROOT = Path(__file__).resolve().parents[2]


class ArgosRuntimeTests(unittest.TestCase):
    def test_cue_recipe_preserves_decimals_and_rejects_token_truncation(self):
        from types import SimpleNamespace
        splitter = runtime.CueSentencizer(SimpleNamespace(encode=lambda value: value.split()))
        self.assertEqual(splitter.split_sentences("3.14 is pi. Hello!你好。世界"), ["3.14 is pi.", "Hello!", "你好。", "世界"])
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_INPUT_TOO_LONG"):
            splitter.split_sentences("word " * 513)

    def test_actual_pivot_and_chinese_variant_provenance(self):
        generic = runtime.ArgosRuntime(ROOT, Path("private-models"), "zh")
        declared = runtime.ArgosRuntime(ROOT, Path("private-models"), "zh-TW")
        english = runtime.ArgosRuntime(ROOT, Path("private-models"), "en")
        self.assertEqual(declared.provenance()["backend_route"], ["zh", "en", "vi"])
        self.assertEqual(declared.provenance()["requested_source_language"], "zh-TW")
        self.assertEqual(generic.root, declared.root)
        self.assertNotEqual(generic.root, english.root)
        self.assertEqual(english.provenance()["backend_route"], ["en", "vi"])

    def test_vietnamese_identity_needs_no_sdk_or_download(self):
        value = runtime.ArgosRuntime(ROOT, Path("models"), "vi")
        with patch.object(runtime, "ensure_model_profile", side_effect=AssertionError("unexpected download")), patch.object(runtime.importlib.metadata, "version", side_effect=AssertionError("unexpected SDK")):
            value.prepare(Path("models"))
            self.assertEqual(value.translate_text("Xin chào"), "Xin chào")
        self.assertEqual(value.provenance()["backend"], "vi-identity-v1")

    def test_unknown_or_auto_source_has_typed_route_failure(self):
        for language in ("auto", "und", "fr"):
            with self.subTest(language=language), self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_UNAVAILABLE"):
                runtime.ArgosRuntime(ROOT, Path("models"), language)

    def test_missing_app_profile_is_an_actionable_typed_error(self):
        with TemporaryDirectory() as directory, self.assertRaisesRegex(TranslationError, "TRANSLATION_PROFILE_MISSING"):
            runtime.ArgosRuntime(Path(directory), Path(directory) / "models", "zh")

    def test_invalid_profile_cannot_create_a_silent_identity_route(self):
        profile = json.loads((ROOT / runtime.PROFILE_PATH).read_text(encoding="utf-8"))
        for change in ({"routes": {"zh": []}}, {"routes": {"zh": ["en-vi"]}}, {"routes": {"zh": ["absent"]}}):
            with self.subTest(change=change), patch.object(runtime, "read_json", return_value={**profile, **change}), self.assertRaisesRegex(TranslationError, "TRANSLATION_PROFILE_INVALID"):
                runtime.ArgosRuntime(ROOT, Path("models"), "zh")

    def test_portable_paths_reject_escape_controls_and_windows_special_names(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../x", "/x", "a\\b", "CON.txt", "a/NUL", "a/b.", "a/b ", "a:b", "a/\x00b", "a/*b"):
                with self.subTest(name=name), self.assertRaisesRegex(TranslationError, "TRANSLATION_PATH_UNSAFE"):
                    runtime.child(root, name)

    def package(self, root, *, extra=None):
        content = {"pair/metadata.json": json.dumps({"from_code": "en", "to_code": "vi", "package_version": "1.9"}).encode(), "pair/model.bin": b"model-data"}
        expected = root / "expected"
        for name, payload in content.items():
            path = expected / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        archive = root / "pair.argosmodel"
        with zipfile.ZipFile(archive, "w") as writer:
            for name, payload in content.items():
                writer.writestr(name, payload)
            if extra is not None:
                writer.writestr(*extra)
        result = {"id": "en-vi", "path": archive.name, "zip_root": "pair", "from_code": "en", "to_code": "vi", "package_version": "1.9", "size_bytes": archive.stat().st_size, "sha256": runtime.digest(archive)}
        result["tree_sha256"] = runtime.tree_hash(expected, result)
        return result

    def test_install_verifies_archive_tree_and_metadata(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.package(root)
            destination = root / "store"
            runtime.install_packages(destination, root, [package])
            self.assertTrue(runtime.valid_package(destination, package))
            (destination / "pair/model.bin").write_bytes(b"tampered")
            self.assertFalse(runtime.valid_package(destination, package))
            runtime.install_packages(destination, root, [package])
            self.assertTrue(runtime.valid_package(destination, package))
            self.assertEqual(len(list(root.glob("store.corrupt-*"))), 1)
            self.assertFalse(list(root.glob(".argos-install-*")))
            self.assertFalse(runtime.valid_package(destination, {**package, "from_code": "zh"}))

    def test_rejected_archive_preserves_previous_model(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = self.package(root)
            destination = root / "store"
            runtime.install_packages(destination, root, [package])
            with self.assertRaisesRegex(TranslationError, "TRANSLATION_PACKAGE_CHECKSUM_MISMATCH"):
                runtime.install_packages(destination, root, [{**package, "sha256": "0" * 64}])
            self.assertTrue(runtime.valid_package(destination, package))

    def test_archive_rejects_path_link_special_file_and_size_quota(self):
        for kind in ("escape", "link", "fifo", "quota", "directories"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                root = Path(directory)
                if kind in {"link", "fifo"}:
                    entry = zipfile.ZipInfo("pair/extra")
                    entry.create_system = 3
                    entry.external_attr = ((stat.S_IFLNK if kind == "link" else stat.S_IFIFO) | 0o777) << 16
                    extra = (entry, b"target")
                else:
                    extra = ("pair/../escape" if kind == "escape" else "pair/extra/", b"")
                package = self.package(root, extra=extra)
                settings = {"MAX_EXPANDED_BYTES": 1} if kind == "quota" else {"MAX_FILES": 2} if kind == "directories" else {}
                with (patch.multiple(runtime, **settings) if settings else nullcontext()), self.assertRaises(TranslationError):
                    runtime.install_packages(root / "store", root, [package])
                self.assertFalse((root / "store").exists())
                self.assertFalse(list(root.glob(".argos-install-*")))

    def test_wrong_runtime_version_fails_before_bootstrap(self):
        value = runtime.ArgosRuntime(ROOT, Path("models"), "en")
        with patch.object(runtime.importlib.metadata, "version", return_value="0.0"), patch.object(runtime, "ensure_model_profile") as bootstrap:
            with self.assertRaisesRegex(TranslationError, "TRANSLATION_RUNTIME_MISMATCH"):
                value.prepare(Path("models"))
            bootstrap.assert_not_called()


if __name__ == "__main__":
    unittest.main()

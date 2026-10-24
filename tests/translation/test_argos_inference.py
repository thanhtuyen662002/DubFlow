from __future__ import annotations

import builtins
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.translation import argos_runtime as runtime
from engine.dubflow.translation.argos_inference import PinnedPackageTranslation
from engine.dubflow.translation.adapter import TranslationError

ROOT = Path(__file__).resolve().parents[2]


def settings():
    return SimpleNamespace(device="cpu", compute_type="int8", inter_threads=1,
                           intra_threads=4, batch_size=32, beam_size=4,
                           debug=False, dev_mode=False,
                           ChunkType=SimpleNamespace(STANZA="STANZA"), chunk_type="STANZA",
                           ModelProvider=SimpleNamespace(OPENNMT="OPENNMT"), model_provider="OPENNMT")


class ArgosInferenceTests(unittest.TestCase):
    def test_native_recipe_lazy_reuse_and_decode_once_per_paragraph(self):
        tokenizer = SimpleNamespace(encode=Mock(side_effect=lambda s: [s]),
                                    decode=Mock(side_effect=lambda tokens: " " + "|".join(tokens)))
        pkg = SimpleNamespace(package_path=Path("private/model-pack"), tokenizer=tokenizer, target_prefix="")
        native = Mock()
        native.translate_batch.side_effect = lambda batch, **kwargs: [
            SimpleNamespace(hypotheses=[["VI:" + tokens[0]], ["unused hypothesis"]]) for tokens in batch
        ]
        extension = ModuleType("ctranslate2")
        extension.Translator = Mock(return_value=native)
        backend = PinnedPackageTranslation(pkg, runtime.CueSentencizer(tokenizer), settings())
        extension.Translator.assert_not_called()
        with patch.dict(sys.modules, {"ctranslate2": extension}):
            self.assertEqual(backend.translate("First. Second!\nThird?"), "VI:First.|VI:Second!\nVI:Third?")
            self.assertEqual(backend.translate("Again."), "VI:Again.")
        extension.Translator.assert_called_once_with(
            str(pkg.package_path / "model"), device="cpu", inter_threads=1,
            intra_threads=4, compute_type="int8")
        self.assertEqual(tokenizer.decode.call_args_list[0].args[0], ["VI:First.", "VI:Second!"])
        self.assertEqual(tokenizer.decode.call_count, 3)
        native.translate_batch.assert_any_call(
            [["First."], ["Second!"]], target_prefix=None, replace_unknowns=True,
            max_batch_size=32, batch_type="tokens", beam_size=4,
            num_hypotheses=1, length_penalty=0.2, return_scores=True)

    def test_target_prefix_and_exact_argos_space_removal(self):
        for prefix, decoded, expected in (
            ("<vi>", "<vi>  xin", " xin"),
            ("<vi>", "other <vi>", "other <vi>"),
            ("", "  xin", " xin"),
            ("", "", ""),
            ("<vi>", "<vi>", ""),
        ):
            with self.subTest(prefix=prefix, decoded=decoded):
                tokenizer = SimpleNamespace(encode=lambda s: [s], decode=lambda tokens: decoded)
                pkg = SimpleNamespace(package_path=Path("private"), tokenizer=tokenizer, target_prefix=prefix)
                backend = PinnedPackageTranslation(pkg, runtime.CueSentencizer(tokenizer), settings())
                backend.translator = Mock()
                backend.translator.translate_batch.return_value = [SimpleNamespace(hypotheses=[["tokens"]])]
                self.assertEqual(backend.translate("One! Two?"), expected)
                self.assertEqual(backend.translator.translate_batch.call_args.kwargs["target_prefix"],
                                 [[prefix], [prefix]] if prefix else None)

    def test_blank_paragraphs_and_input_limits_retain_typed_failures(self):
        tokenizer = SimpleNamespace(encode=lambda s: s.split(), decode=lambda tokens: "valid")
        pkg = SimpleNamespace(package_path=Path("private"), tokenizer=tokenizer, target_prefix="")
        backend = PinnedPackageTranslation(pkg, runtime.CueSentencizer(tokenizer), settings())
        backend.translator = Mock()
        backend.translator.translate_batch.return_value = [SimpleNamespace(hypotheses=[["tokens"]])]
        for text in ("", "\nvalid", "valid\n\nvalid", "word " * 513, "x" * 16385, "Hi!" * 129):
            with self.subTest(text=text[:30]), self.assertRaisesRegex(TranslationError, "TRANSLATION_INPUT_TOO_LONG"):
                backend.translate(text)


class ArgosPreparationTests(unittest.TestCase):
    @contextmanager
    def prepared_sdk(self, language="en"):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backend = runtime.ArgosRuntime(ROOT, root, language)
            backend.root.mkdir(parents=True)
            installed = []
            for item in backend.packages:
                path = backend.root / item["zip_root"]
                path.mkdir()
                installed.append(SimpleNamespace(
                    from_code=item["from_code"], to_code=item["to_code"],
                    package_version=item["package_version"], package_path=path,
                    tokenizer=SimpleNamespace(encode=lambda s: s.split()), target_prefix=""))
            config = settings()
            private = backend.root.parent / (backend.root.name + "-runtime")
            config.package_data_dir = backend.root
            config.data_dir = private / "data/argos-translate"
            config.config_dir = private / "config/argos-translate"
            config.cache_dir = private / "cache/argos-translate"
            sdk = ModuleType("argostranslate")
            sdk.settings = config
            sdk.package = SimpleNamespace(get_installed_packages=lambda: installed)
            version_pins = {"argostranslate": runtime.RUNTIME_VERSION, **runtime.COMPONENT_VERSIONS}
            original_import = builtins.__import__

            def import_guard(name, globals=None, locals=None, fromlist=(), level=0):
                if name.split(".")[0] in {"torch", "stanza", "spacy"} or name in {
                    "argostranslate.translate", "argostranslate.sbd"
                } or name == "argostranslate" and set(fromlist) & {"translate", "sbd"}:
                    raise AssertionError("unused inference dependency imported: " + name)
                return original_import(name, globals, locals, fromlist, level)

            with patch.dict(os.environ), patch.dict(sys.modules, {"argostranslate": sdk}), \
                 patch.object(runtime.importlib.metadata, "version", side_effect=version_pins.__getitem__), \
                 patch.object(backend, "_bootstrap_packages"), patch.object(runtime, "valid_package", return_value=True), \
                 patch("builtins.__import__", side_effect=import_guard):
                yield backend, root, config, installed

    def test_both_routes_prepare_without_translate_sbd_or_heavy_modules(self):
        for language in ("en", "zh"):
            with self.subTest(language=language), self.prepared_sdk(language) as (backend, root, config, installed):
                before = backend.provenance()
                backend.prepare(root)
                self.assertEqual(backend.provenance(), before)
                self.assertEqual(len(backend._translators), len(installed))
                self.assertTrue(all(isinstance(value, PinnedPackageTranslation) for value in backend._translators))
                self.assertTrue(all(value.translator is None for value in backend._translators))
                self.assertEqual(os.environ["ARGOS_CHUNK_TYPE"], "STANZA")

    def test_preinitialized_settings_cannot_bypass_recipe(self):
        for key, value in (("device", "cuda"), ("compute_type", "auto"),
                           ("chunk_type", "MINISBD"), ("model_provider", "REMOTE"),
                           ("beam_size", 1), ("batch_size", 16), ("inter_threads", 2),
                           ("intra_threads", 8), ("debug", True), ("dev_mode", True)):
            with self.subTest(key=key), self.prepared_sdk() as (backend, root, config, installed):
                setattr(config, key, value)
                with self.assertRaisesRegex(TranslationError, "TRANSLATION_RUNTIME_MISMATCH"):
                    backend.prepare(root)
                self.assertIsNone(backend._translators)

    def test_external_package_or_auxiliary_cache_still_fails(self):
        for key in ("package_data_dir", "data_dir", "config_dir", "cache_dir"):
            with self.subTest(key=key), self.prepared_sdk() as (backend, root, config, installed):
                setattr(config, key, root / "external")
                with self.assertRaisesRegex(TranslationError, "TRANSLATION_CACHE_UNSAFE"):
                    backend.prepare(root)

    def test_wrong_installed_language_version_still_fails(self):
        with self.prepared_sdk() as (backend, root, config, installed):
            installed[0].package_version = "wrong"
            with self.assertRaisesRegex(TranslationError, "TRANSLATION_PACKAGE_MISMATCH"):
                backend.prepare(root)


if __name__ == "__main__":
    unittest.main()

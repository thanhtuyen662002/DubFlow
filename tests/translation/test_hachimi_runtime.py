from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.dubflow.models.runtime import load_profile
from engine.dubflow.translation import hachimi_runtime as runtime
from engine.dubflow.translation.adapter import TranslationError
from engine.dubflow.translation.argos_runtime import ArgosRuntime


ROOT = Path(__file__).resolve().parents[2]


class FakeTokenizer:
    def encode(self, text, out_type=str):
        return list(text)

    def decode(self, tokens):
        return "".join(tokens)


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **options):
        self.calls.append((batch, options))
        return [SimpleNamespace(hypotheses=[["<s>", *("vi:" + "".join(tokens[:-1])), "</s>", "<pad>"]]) for tokens in batch]


def ready(value: runtime.HachimiRuntime) -> FakeTranslator:
    translator = FakeTranslator()
    value._translator, value._source, value._target = translator, FakeTokenizer(), FakeTokenizer()
    return translator


class HachimiRuntimeTests(unittest.TestCase):
    def test_profile_is_a_valid_pinned_download_profile(self):
        profile_id, artifacts = load_profile(ROOT / runtime.PROFILE_PATH)
        self.assertEqual(profile_id, "dubflow-hachimi-mt-60-zh-vi-v1")
        self.assertEqual({item.relative_path.split("/")[-1] for item in artifacts}, {"config.json", "model.bin", "shared_vocabulary.json", "source.spm", "target.spm", "NOTICE-hachimi-mt-60.md"})
        document = json.loads((ROOT / runtime.PROFILE_PATH).read_text(encoding="utf-8"))
        self.assertTrue(all(item["path"].startswith(document["model_dir"] + "/") for item in document["artifacts"]))
        self.assertEqual(document["license"], "CC-BY-4.0")
        self.assertTrue((ROOT / "docs/licenses/hachimi-mt-60.md").is_file())

    def test_engine_selection_keeps_argos_default_and_for_non_chinese(self):
        self.assertIsInstance(runtime.create_runtime("argos", ROOT, Path("models"), "zh"), ArgosRuntime)
        self.assertIsInstance(runtime.create_runtime("hachimi", ROOT, Path("models"), "en"), ArgosRuntime)
        self.assertIsInstance(runtime.create_runtime("hachimi", ROOT, Path("models"), "vi"), ArgosRuntime)
        for language in ("zh", "zh-CN", "zh-TW"):
            self.assertIsInstance(runtime.create_runtime("hachimi", ROOT, Path("models"), language), runtime.HachimiRuntime)
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ENGINE_UNKNOWN"):
            runtime.create_runtime("gemini", ROOT, Path("models"), "zh")
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_ROUTE_UNAVAILABLE"):
            runtime.HachimiRuntime(ROOT, Path("models"), "en")

    def test_provenance_differs_from_argos_so_caches_invalidate(self):
        hachimi = runtime.HachimiRuntime(ROOT, Path("models"), "zh").provenance()
        argos = ArgosRuntime(ROOT, Path("models"), "zh").provenance()
        self.assertNotEqual(hachimi, argos)
        self.assertEqual(hachimi["backend"], "hachimi-mt-60-ct2-int8-v1")
        self.assertEqual(hachimi["backend_route"], ["zh", "vi"])
        self.assertEqual(hachimi["license"], "CC-BY-4.0")
        self.assertEqual(hachimi["settings"]["repetition_penalty"], 1.2)

    def test_model_resolves_under_private_model_root(self):
        value = runtime.HachimiRuntime(ROOT, Path("models-root"), "zh")
        self.assertEqual(value.model_dir, Path("models-root").absolute() / "translation" / "hachimi-mt-60-zh-vi-v1")

    def test_translate_splits_sentences_appends_eos_and_strips_special_tokens(self):
        value = runtime.HachimiRuntime(ROOT, Path("models"), "zh")
        translator = ready(value)
        self.assertEqual(value.translate_text("你好。今天吃什么？"), "vi:你好。 vi:今天吃什么？")
        batch, options = translator.calls[0]
        self.assertEqual(batch[0][-1], "</s>")
        self.assertEqual(len(batch), 2)
        self.assertEqual(options, runtime.DECODE_OPTIONS)

    def test_translate_requires_prepare_and_bounds_input(self):
        value = runtime.HachimiRuntime(ROOT, Path("models"), "zh")
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_RUNTIME_MISSING"):
            value.translate_text("你好")
        ready(value)
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_INPUT_TOO_LONG"):
            value.translate_text("长" * (runtime.MAX_SOURCE_TOKENS + 1))
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_INPUT_TOO_LONG"):
            value.translate_text("   ")

    def test_empty_output_is_an_error(self):
        value = runtime.HachimiRuntime(ROOT, Path("models"), "zh")
        ready(value)
        value._translator = SimpleNamespace(translate_batch=lambda batch, **options: [SimpleNamespace(hypotheses=[["</s>"]])])
        with self.assertRaisesRegex(TranslationError, "TRANSLATION_EMPTY"):
            value.translate_text("你好")

    def test_prepare_checks_pins_before_downloading(self):
        value = runtime.HachimiRuntime(ROOT, Path("models"), "zh")
        with patch.object(runtime.importlib.metadata, "version", return_value="0.0.0"), patch.object(runtime, "ensure_model_profile", side_effect=AssertionError("unexpected download")):
            with self.assertRaisesRegex(TranslationError, "TRANSLATION_RUNTIME_MISMATCH"):
                value.prepare(Path("models"))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from engine.dubflow.worker import production_job
from engine.dubflow.worker.production_job import AI_KEY_ENV, TRANSLATION_SETTINGS_FILE, _translation_settings


class TranslationSettingsTests(unittest.TestCase):
    def setUp(self):
        production_job._AI_KEY = None

    def tearDown(self):
        production_job._AI_KEY = None
        os.environ.pop(AI_KEY_ENV, None)

    def load(self, document, key=None):
        with TemporaryDirectory() as temp:
            if document is not None:
                (Path(temp) / TRANSLATION_SETTINGS_FILE).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            if key is not None:
                os.environ[AI_KEY_ENV] = key
            warnings: list[str] = []
            return _translation_settings(SimpleNamespace(output_dir=Path(temp)), warnings), warnings

    def test_no_file_means_offline_and_no_sensitive_words(self):
        settings, warnings = self.load(None)
        self.assertEqual((settings.route, settings.sensitive, warnings), (None, None, []))

    def test_provider_model_and_key_make_a_route_and_the_key_leaves_the_environment(self):
        settings, warnings = self.load({"schema_version": 1, "title": "Mẹ ơi",
                                        "ai": {"provider": "deepseek", "model": "deepseek-flash"},
                                        "sensitive": {"mode": "beep", "words": [{"from": "chết", "to": "chớt"}]}}, key="sk-abcdefgh1234")
        self.assertEqual((settings.route.provider, settings.route.model, settings.route.key), ("deepseek", "deepseek-flash", "sk-abcdefgh1234"))
        self.assertNotIn(AI_KEY_ENV, os.environ)
        self.assertEqual(settings.sensitive.mode, "beep")
        self.assertEqual(settings.title, "Mẹ ơi")
        self.assertEqual(warnings, [])

    def test_a_missing_key_or_bad_entries_warn_and_keep_the_offline_translation(self):
        settings, warnings = self.load({"schema_version": 1, "ai": {"provider": "openai"}, "sensitive": {"mode": "loud", "words": []}})
        self.assertIsNone(settings.route)
        self.assertIsNone(settings.sensitive)
        self.assertTrue(warnings[0].startswith("AI_TRANSLATION_SKIPPED:"))
        self.assertTrue(warnings[1].startswith("SENSITIVE_WORDS_INVALID:"))
        settings, warnings = self.load({"schema_version": 9})
        self.assertIsNone(settings.route)
        self.assertTrue(warnings[0].startswith("TRANSLATION_SETTINGS_INVALID:"))


if __name__ == "__main__":
    unittest.main()

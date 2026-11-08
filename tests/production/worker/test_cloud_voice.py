"""cloud_voice.json: a Vibi.pro voice for the dubbing, never a reason to fail the job (#632)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from engine.dubflow.tts import vibi
from engine.dubflow.worker import production_job

SETTINGS = {"provider": "elevenlabs", "voice_id": "VR6AewLTigWG4xSOukaG", "voice_name": "Arnold", "model_id": "eleven_multilingual_v2",
            "language_code": "vi", "stability": 0.45, "similarity_boost": 0.78, "style": 0.15}


class FakeClient:
    balance = 500
    error: vibi.VibiError | None = None

    def __init__(self, key):
        if not key:
            raise vibi.VibiError("KEY_MISSING", "Chưa có khóa Vibi.pro")
        self.key = key

    def me(self):
        if FakeClient.error:
            raise FakeClient.error
        return {"credit_balance": FakeClient.balance}


class CloudVoiceTests(unittest.TestCase):
    def setUp(self):
        FakeClient.balance, FakeClient.error = 500, None

    def run_with(self, document, key="sk_test_1234567890", dubbing=True):
        with TemporaryDirectory() as temp:
            if document is not None:
                Path(temp, production_job.CLOUD_VOICE_FILE).write_text(json.dumps(document), encoding="utf-8")
            config = SimpleNamespace(output_dir=Path(temp), enable_dubbing=dubbing)
            warnings: list[str] = []
            env = {production_job.VIBI_KEY_ENV: key} if key else {}
            with mock.patch.dict(os.environ, env), mock.patch.object(vibi, "VibiClient", FakeClient):
                result = production_job._cloud_voice(config, warnings)
                self.assertNotIn(production_job.VIBI_KEY_ENV, os.environ)  # Read once, gone from the environment.
            return result, warnings

    def test_chosen_voice_and_key_reach_the_dubbing(self):
        result, warnings = self.run_with({"schema_version": 1, "vibi": SETTINGS})
        settings, key = result
        self.assertEqual((settings.voice_id, settings.stability, key), ("VR6AewLTigWG4xSOukaG", 0.45, "sk_test_1234567890"))
        self.assertEqual(warnings, [])

    def test_no_file_or_no_dubbing_keeps_the_built_in_voice_silently(self):
        self.assertEqual(self.run_with(None), (None, []))
        self.assertEqual(self.run_with({"schema_version": 1, "vibi": SETTINGS}, dubbing=False), (None, []))

    def test_missing_key_refused_key_empty_balance_and_bad_file_fall_back_with_a_warning(self):
        cases = [({"schema_version": 1, "vibi": SETTINGS}, "", None, "Chưa có khóa"),
                 ({"schema_version": 1, "vibi": SETTINGS}, "sk_test_1", vibi.VibiError("KEY_INVALID", "Khóa Vibi.pro không đúng"), "không đúng"),
                 ({"schema_version": 1, "vibi": {**SETTINGS, "stability": 4}}, "sk_test_1", None, "Stability"),
                 ({"schema_version": 2}, "sk_test_1", None, "schema_version")]
        for document, key, error, expected in cases:
            with self.subTest(expected=expected):
                FakeClient.error = error
                result, warnings = self.run_with(document, key)
                self.assertIsNone(result)
                self.assertEqual(len(warnings), 1)
                self.assertTrue(warnings[0].startswith("CLOUD_VOICE_SKIPPED"))
                self.assertIn(expected, warnings[0])
        FakeClient.error, FakeClient.balance = None, 0
        result, warnings = self.run_with({"schema_version": 1, "vibi": SETTINGS})
        self.assertIsNone(result)
        self.assertIn("hết credit", warnings[0])


if __name__ == "__main__":
    unittest.main()

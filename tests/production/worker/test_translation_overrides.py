from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.worker.production_job import (
    ProductionJobError,
    TRANSLATION_OVERRIDES_FILE,
    TextCue,
    _apply_translation_overrides,
)

CUES = (
    TextCue("asr-1-2-a", 0, 1000, "你好", "Xin chào"),
    TextCue("asr-3-4-b", 1000, 2500, "谢谢大家", "Cảm ơn mọi người rất rất nhiều"),
)


class TranslationOverrideTests(unittest.TestCase):
    def write(self, directory: Path, document) -> None:
        (directory / TRANSLATION_OVERRIDES_FILE).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    def test_without_file_cues_are_unchanged_and_identity_is_none(self):
        with TemporaryDirectory() as temp:
            cues, identity = _apply_translation_overrides(CUES, Path(temp))
        self.assertEqual(cues, CUES)
        self.assertIsNone(identity)

    def test_override_replaces_only_named_cue_and_normalises_whitespace(self):
        with TemporaryDirectory() as temp:
            self.write(Path(temp), {"schema_version": 1, "cues": {"asr-3-4-b": "  Cảm ơn\n mọi người "}})
            cues, identity = _apply_translation_overrides(CUES, Path(temp))
        self.assertEqual([cue.translated_text for cue in cues], ["Xin chào", "Cảm ơn mọi người"])
        self.assertEqual([cue.source_text for cue in cues], ["你好", "谢谢大家"])
        self.assertTrue(identity.startswith("sha256:"))

    def test_identity_changes_with_text(self):
        identities = []
        for text in ("Cảm ơn", "Cảm ơn nhé"):
            with TemporaryDirectory() as temp:
                self.write(Path(temp), {"schema_version": 1, "cues": {"asr-3-4-b": text}})
                identities.append(_apply_translation_overrides(CUES, Path(temp))[1])
        self.assertNotEqual(*identities)

    def test_invalid_documents_are_refused(self):
        for document in ({"schema_version": 2, "cues": {}}, {"schema_version": 1, "cues": []},
                         {"schema_version": 1, "cues": {"asr-3-4-b": "   "}},
                         {"schema_version": 1, "cues": {"asr-3-4-b": "x" * 2001}},
                         {"schema_version": 1, "cues": {"unknown-cue": "Xin chào"}}):
            with self.subTest(document=str(document)[:60]), TemporaryDirectory() as temp:
                self.write(Path(temp), document)
                with self.assertRaises(ProductionJobError) as context:
                    _apply_translation_overrides(CUES, Path(temp))
                self.assertEqual(context.exception.code, "TRANSLATION_OVERRIDES_INVALID")


if __name__ == "__main__":
    unittest.main()

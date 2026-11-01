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
    _review_voice,
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



class ReviewVoiceTests(unittest.TestCase):
    def run_voice(self, document):
        with TemporaryDirectory() as temp:
            (Path(temp) / TRANSLATION_OVERRIDES_FILE).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            subtitles, _ = _apply_translation_overrides(CUES, Path(temp))
            return subtitles, _review_voice(subtitles, Path(temp))

    def test_reviews_without_voice_keys_speak_the_subtitle_text_with_the_old_identity(self):
        with TemporaryDirectory() as temp:
            self.assertEqual(_review_voice(CUES, Path(temp)), (CUES, frozenset(), None))
        subtitles, (spoken, revoice, identity) = self.run_voice({"schema_version": 1, "cues": {"asr-1-2-a": "Chào bạn"}})
        self.assertEqual(spoken, subtitles)
        self.assertEqual((revoice, identity), (frozenset(), None))

    def test_an_unmarked_edit_keeps_its_earlier_speech(self):
        subtitles, (spoken, revoice, identity) = self.run_voice({
            "schema_version": 1, "cues": {"asr-1-2-a": "Chào bạn", "asr-3-4-b": "Cảm ơn nhé"},
            "voice": {"asr-1-2-a": "Xin  chào"}, "revoice": ["asr-3-4-b"]})
        self.assertEqual([cue.translated_text for cue in subtitles], ["Chào bạn", "Cảm ơn nhé"])
        self.assertEqual([cue.translated_text for cue in spoken], ["Xin chào", "Cảm ơn nhé"])
        self.assertEqual(revoice, frozenset({"asr-3-4-b"}))
        self.assertTrue(identity.startswith("sha256:"))

    def test_marking_a_line_changes_the_identity_even_with_the_same_text(self):
        base = {"schema_version": 1, "cues": {}, "voice": {}}
        unmarked = self.run_voice(dict(base, revoice=[]))[1][2]
        marked = self.run_voice(dict(base, revoice=["asr-1-2-a"]))[1][2]
        self.assertNotEqual(unmarked, marked)

    def test_invalid_voice_keys_are_refused(self):
        for extra in ({"voice": []}, {"voice": {"asr-1-2-a": " "}}, {"voice": {"asr-1-2-a": 3}}, {"voice": {"nope": "Xin"}},
                      {"revoice": "asr-1-2-a"}, {"revoice": [1]}, {"revoice": ["nope"]}):
            with self.subTest(extra=extra), self.assertRaises(ProductionJobError) as context:
                self.run_voice(dict({"schema_version": 1, "cues": {}}, **extra))
            self.assertEqual(context.exception.code, "TRANSLATION_OVERRIDES_INVALID")


if __name__ == "__main__":
    unittest.main()

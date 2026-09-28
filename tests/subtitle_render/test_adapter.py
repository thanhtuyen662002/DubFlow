from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from engine.dubflow.asr import TimeBase, TimeInterval, TimePoint
from engine.dubflow.subtitle.render import (
    LocalSubtitleComposer,
    SubtitleConfig,
    SubtitleError,
    SubtitleInput,
    SubtitleProvenance,
    parse_subtitle_json,
    validate_subtitle_document,
)


BASE = TimeBase(1, 1000)
INPUT_HASH = "sha256:" + "1" * 64


def point(ticks: int) -> TimePoint:
    return TimePoint(ticks, BASE)


def segment(identifier: str, text: str, start: int, end: int, confidence: float = 0.9) -> SubtitleInput:
    return SubtitleInput(identifier, text, point(start), point(end), confidence)


def composer(config: SubtitleConfig | None = None, *, input_hash: str = INPUT_HASH) -> LocalSubtitleComposer:
    config = config or SubtitleConfig(1080, 1920)
    return LocalSubtitleComposer(
        config=config,
        provenance=SubtitleProvenance("dubflow-subtitle-test", "1.0.0", config.to_hash(), input_hash, "timeline-v1"),
    )


class SubtitleComposerTests(unittest.TestCase):
    def test_portrait_srt_and_ass_preserve_identity_and_use_safe_area(self) -> None:
        items = (
            segment("u-1", "Xin chào, Nguyễn Văn A 2.0!", 100, 1200),
            segment("u-2", "Dòng phụ đề thứ hai.", 1400, 2600),
        )
        result = composer().compose(items, input_hash=INPUT_HASH)
        self.assertEqual(result.layout.aspect, "portrait")
        self.assertEqual([item.source_utterance_id for item in result.cues], ["u-1", "u-2"])
        self.assertIn("Xin chào, Nguyễn Văn A 2.0!", result.srt)
        self.assertIn("[Events]", result.ass)
        self.assertIn("Dialogue: 0,", result.ass)
        self.assertTrue(all(item.start.ticks >= 0 for item in result.cues))
        validate_subtitle_document(result.to_dict())
        self.assertEqual(parse_subtitle_json(result.to_json())["target_language"], "vi")

    def test_landscape_and_square_layouts_have_explicit_safe_margins(self) -> None:
        landscape = composer(SubtitleConfig(1920, 1080, position="top-center")).compose(
            (segment("land", "landscape", 0, 1000),), input_hash=INPUT_HASH
        )
        square = composer(SubtitleConfig(1000, 1000, position="middle-center")).compose(
            (segment("square", "square", 0, 1000),), input_hash=INPUT_HASH
        )
        self.assertEqual(landscape.layout.aspect, "landscape")
        self.assertEqual(landscape.layout.position, "top-center")
        self.assertEqual(square.layout.aspect, "square")
        self.assertGreater(landscape.layout.margin_left, 0)
        self.assertGreater(square.layout.margin_top, 0)

    def test_line_wrapping_and_reading_speed_warning_are_deterministic(self) -> None:
        config = SubtitleConfig(1080, 1920, max_line_chars=12, max_lines=2, max_chars_per_second=10, minimum_duration_ms=500)
        result = composer(config).compose((segment("long", "one two three four five six seven", 0, 200),), input_hash=INPUT_HASH)
        self.assertLessEqual(len(result.cues[0].lines), 2)
        self.assertTrue(all(len(line) <= 12 for line in result.cues[0].lines))
        self.assertTrue(any("reading-speed" in warning for warning in result.warnings))
        self.assertIn("seven", result.srt)

    def test_negative_source_anchor_is_shifted_and_recorded_without_negative_srt(self) -> None:
        result = composer().compose((segment("negative", "anchored", -500, 500),), input_hash=INPUT_HASH)
        self.assertEqual(result.timeline_offset.ticks, 500)
        self.assertEqual(result.cues[0].start.ticks, 0)
        self.assertTrue(result.srt.splitlines()[1].startswith("00:00:00,000"))
        self.assertTrue(any("shifted" in warning for warning in result.warnings))

    def test_overlapping_cues_and_timebase_mismatch_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "OVERLAPPING_CUES"):
            composer().compose((segment("a", "a", 0, 1000), segment("b", "b", 900, 1500)), input_hash=INPUT_HASH)
        other_base = TimeBase(1, 48000)
        with self.assertRaisesRegex(ValueError, "TIMELINE_TIME_BASE_MISMATCH"):
            composer().compose((segment("a", "a", 0, 1000), SubtitleInput("b", "b", TimePoint(1, other_base), TimePoint(2, other_base))), input_hash=INPUT_HASH)

    def test_font_fallback_is_documented_and_ass_uses_selected_font(self) -> None:
        config = SubtitleConfig(1080, 1920, font_name="Missing Font", font_fallbacks=("Arial", "sans-serif"), font_available=False)
        result = composer(config).compose((segment("font", "font fallback", 0, 1000),), input_hash=INPUT_HASH)
        self.assertEqual(result.layout.font_name, "Arial")
        self.assertIn("Arial", result.ass)
        self.assertTrue(any("font fallback" in warning for warning in result.warnings))

    def test_duplicate_source_ids_and_wire_noncanonical_ticks_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SOURCE_ID"):
            composer().compose((segment("same", "one", 0, 1000), segment("same", "two", 1200, 2200)), input_hash=INPUT_HASH)
        result = composer().compose((segment("wire", "wire", 0, 1000),), input_hash=INPUT_HASH)
        invalid = result.to_dict()
        invalid["cues"][0]["start"]["ticks"] = "-0"
        with self.assertRaisesRegex(ValueError, "canonical decimal"):
            validate_subtitle_document(invalid)
        duplicate = result.to_json().replace('"kind":"subtitle_document"', '"kind":"subtitle_document","kind":"subtitle_document"', 1)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_FIELD"):
            parse_subtitle_json(duplicate)

    def test_schema_document_matches_wire_surface(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "contracts" / "subtitles" / "schema-v1.json").read_text(encoding="utf-8"))
        result = composer().compose((segment("schema", "schema", 0, 1000),), input_hash=INPUT_HASH)
        self.assertEqual(set(result.to_dict()), set(schema["required"]))
        self.assertEqual(schema["$id"], "https://dubflow.local/contracts/subtitles/schema-v1.json")


if __name__ == "__main__":
    unittest.main()

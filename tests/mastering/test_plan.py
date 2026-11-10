"""The mastering plan contract (ADR-0052): validation, mapping, persistence and the schema file."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import plan as m

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "mastering"
SCHEMA = ROOT / "contracts" / "mastering" / "schema-v1.json"


def provenance(**inputs: str) -> m.Provenance:
    return m.Provenance("test", "1", "r1", tuple(sorted(inputs.items())))


def simple(*spans: tuple[int, int, int], duration: int | None = None) -> m.Plan:
    """A plan of footage pieces (source start, source end, timeline start) of one 20 s source."""
    segments = tuple(m.Segment(f"s{i}", "v", m.Span(a, b), m.Span(at, at + b - a)) for i, (a, b, at) in enumerate(spans))
    total = duration if duration is not None else segments[-1].timeline.end
    return m.Plan("p", "product_showcase", m.Options("balanced"), total, (m.Source("v", 20_000, 1920, 1080, 30, 1),),
                  segments, provenance())


class FixtureTests(unittest.TestCase):
    def test_valid_fixtures_load_and_round_trip_unchanged(self):
        plan_text = (FIXTURES / "valid" / "product-plan.json").read_text(encoding="utf-8")
        loaded = m.parse(plan_text)
        self.assertEqual(json.loads(loaded.to_json()), m.Plan.from_dict(json.loads(loaded.to_json())).to_dict())
        self.assertEqual(loaded.segments[0].framing.protect[0].label, "product")
        self.assertEqual(loaded.sources[1].rights.note, "")  # optional note defaults to empty
        options = m.Options.from_dict(json.loads((FIXTURES / "valid" / "options-balanced.json").read_text()))
        self.assertEqual(options, m.Options("balanced", "cinematic"))

    def test_every_invalid_fixture_is_rejected_with_a_typed_error(self):
        expected = {
            "float-ticks": "INVALID_TICKS", "leading-zero-ticks": "INVALID_TICKS", "timeline-gap": "TIMELINE_GAP",
            "timeline-overlap": "TIMELINE_OVERLAP", "short-coverage": "TIMELINE_GAP", "outside-source": "OUTSIDE_SOURCE",
            "retimed": "RETIMED_SEGMENT", "flip-effect": "FORBIDDEN_EFFECT", "photo-not-still": "INVALID_STILL",
            "unknown-key": "INVALID_DOCUMENT", "version-2": "UNSUPPORTED_VERSION", "bool-confidence": "INVALID_INTEGER",
            "zoom-too-far": "INVALID_INTEGER", "non-reduced-time-base": "NON_REDUCED_TIME_BASE",
            "unknown-source": "UNKNOWN_SOURCE", "duplicate-segment": "DUPLICATE_ID",
        }
        files = sorted((FIXTURES / "invalid").glob("*.json"))
        self.assertEqual({path.stem for path in files}, set(expected))
        for path in files:
            with self.subTest(path.stem), self.assertRaises(m.PlanError) as caught:
                m.parse(path.read_text(encoding="utf-8"))
            self.assertEqual(caught.exception.code, expected[path.stem], path.stem)

    def test_duplicate_keys_and_nan_are_not_json_plans(self):
        text = (FIXTURES / "valid" / "product-plan.json").read_text(encoding="utf-8")
        with self.assertRaises(m.PlanError) as caught:
            m.parse(text.replace('"plan_id": "product-1"', '"plan_id": "product-1", "plan_id": "x"', 1))
        self.assertEqual(caught.exception.code, "DUPLICATE_KEY")
        with self.assertRaises(m.PlanError):
            m.parse(text.replace('"confidence_milli": 820', '"confidence_milli": NaN', 1))


class SchemaTests(unittest.TestCase):
    """The JSON Schema and the Python reference describe the same document."""

    def setUp(self):
        self.defs = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]

    def test_required_keys_match_what_the_model_writes(self):
        plan = m.parse((FIXTURES / "valid" / "product-plan.json").read_text(encoding="utf-8"))
        written = plan.to_dict()
        self.assertEqual(set(self.defs["mastering_plan"]["required"]), set(written))
        self.assertEqual(set(self.defs["mastering_plan"]["properties"]), set(written))
        self.assertEqual(set(self.defs["segment"]["properties"]), set(written["segments"][0]))
        self.assertEqual(set(self.defs["source"]["properties"]), set(written["sources"][0]))
        self.assertEqual(set(self.defs["framing"]["properties"]), set(written["segments"][0]["framing"]))

    def test_enumerations_match_the_model(self):
        props = self.defs["mastering_plan"]["properties"]
        self.assertEqual(tuple(props["workflow"]["enum"]), m.WORKFLOWS)
        self.assertEqual(tuple(self.defs["level"]["enum"]), m.LEVELS)
        self.assertEqual(tuple(self.defs["style"]["enum"]), m.STYLES)
        segment = self.defs["segment"]["properties"]
        self.assertEqual(tuple(segment["role"]["enum"]), m.ROLES)
        self.assertEqual(tuple(segment["audio_mode"]["enum"]), m.AUDIO_MODES)
        self.assertEqual(tuple(segment["effects"]["items"]["enum"]), m.EFFECTS)
        self.assertFalse(set(m.FORBIDDEN_EFFECTS) & set(m.EFFECTS))


class MappingTests(unittest.TestCase):
    def test_timeline_maps_to_source_and_back_exactly(self):
        plan = simple((1000, 4000, 0), (8000, 9000, 3000), (2000, 3000, 4000))
        self.assertEqual(plan.to_source(0), ("v", 1000))
        self.assertEqual(plan.to_source(2999), ("v", 3999))
        self.assertEqual(plan.to_source(3000), ("v", 8000))  # an end belongs to the next segment
        self.assertEqual(plan.to_source(4500), ("v", 2500))
        self.assertEqual(plan.to_timeline("v", 2500), [1500, 4500])  # footage shown twice
        self.assertEqual(plan.to_timeline("v", 5000), [])
        for tick in range(0, plan.duration, 7):
            source_id, at = plan.to_source(tick)
            self.assertIn(tick, plan.to_timeline(source_id, at))
        with self.assertRaises(m.PlanError):
            plan.to_source(plan.duration)

    def test_rescale_is_integer_and_explicit_about_rounding(self):
        ntsc = m.TimeBase(1001, 30000)  # one frame of 29.97 fps
        self.assertEqual(m.rescale(30, ntsc, m.MILLISECONDS, "floor"), 1001)
        self.assertEqual(m.rescale(1, ntsc, m.MILLISECONDS, "nearest"), 33)
        self.assertEqual(m.rescale(1, ntsc, m.MILLISECONDS, "ceil"), 34)
        with self.assertRaises(m.PlanError):
            m.rescale(1, ntsc, m.MILLISECONDS)
        self.assertEqual(m.rescale(3, m.MILLISECONDS, m.TimeBase(1, 48000)), 144)
        self.assertEqual(m.to_milliseconds(90_000, m.TimeBase(1, 90_000)), 1000)

    def test_a_still_shows_one_moment_and_a_moving_segment_keeps_its_speed(self):
        with self.assertRaises(m.PlanError) as caught:
            m.Segment("s", "v", m.Span(0, 2000), m.Span(0, 1000))
        self.assertEqual(caught.exception.code, "RETIMED_SEGMENT")
        still = m.Segment("s", "v", m.Span(700, 700), m.Span(0, 1000), role="still")
        plan = m.Plan("p", "product_showcase", m.Options("light"), 1000, (m.Source("v", 2000, 640, 360),), (still,), provenance())
        self.assertEqual(plan.to_source(999), ("v", 700))
        self.assertEqual(plan.to_timeline("v", 700), [])


class PersistenceTests(unittest.TestCase):
    def test_save_is_atomic_and_load_matching_needs_the_same_inputs(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "work" / "mastering_plan.json"
            plan = m.Plan("p", "product_showcase", m.Options("balanced"), 3000, (m.Source("v", 20_000, 1920, 1080),),
                          (m.Segment("s0", "v", m.Span(0, 3000), m.Span(0, 3000)),), provenance(v="c" * 64))
            m.save(path, plan)
            self.assertFalse(path.with_name(path.name + ".partial").exists())
            self.assertEqual(m.load(path), plan)
            same = dict(workflow="product_showcase", options=m.Options("balanced"), planner_version="1")
            self.assertEqual(m.load_matching(path, inputs={"v": "c" * 64}, **same), plan)
            self.assertIsNone(m.load_matching(path, inputs={"v": "d" * 64}, **same))
            self.assertIsNone(m.load_matching(path, inputs={"v": "c" * 64}, **{**same, "planner_version": "2"}))
            self.assertIsNone(m.load_matching(path, inputs={"v": "c" * 64}, **{**same, "options": m.Options("dynamic")}))
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(m.load_matching(path, inputs={"v": "c" * 64}, **same))
            self.assertIsNone(m.load_matching(Path(folder) / "missing.json", inputs={}, **same))

    def test_identity_follows_size_mtime_and_recipe(self):
        with TemporaryDirectory() as folder:
            media = Path(folder) / "a.mp4"
            media.write_bytes(b"x" * 10)
            first = m.file_identity(media, "r1")
            self.assertEqual(first, m.file_identity(media, "r1"))
            self.assertNotEqual(first, m.file_identity(media, "r2"))
            media.write_bytes(b"x" * 11)
            self.assertNotEqual(first, m.file_identity(media, "r1"))


class OptionsTests(unittest.TestCase):
    def test_absent_options_are_off(self):
        self.assertEqual(m.Options.from_dict(None), m.OFF)
        self.assertFalse(m.OFF.enabled)
        self.assertEqual(m.Options.from_dict({"schema_version": 1, "level": "light"}), m.Options("light", "natural"))

    def test_bad_options_are_rejected(self):
        for value in ({"schema_version": 2, "level": "light"}, {"schema_version": 1, "level": "max"},
                      {"schema_version": 1, "level": "light", "style": "glitch"}, {"schema_version": 1, "level": "light", "x": 1},
                      {"schema_version": True, "level": "light"}, [], "balanced",
                      {"schema_version": 1, "level": "dynamic", "enable_climax_hook": "yes"}):
            with self.subTest(value=value), self.assertRaises(m.PlanError):
                m.Options.from_dict(value)

    def test_the_climax_hook_is_off_unless_asked_and_round_trips(self):
        self.assertFalse(m.Options("dynamic").enable_climax_hook)
        self.assertNotIn("enable_climax_hook", m.Options("dynamic").to_dict())  # earlier documents stay byte-identical
        asked = m.Options.from_dict({"schema_version": 1, "level": "dynamic", "enable_climax_hook": True})
        self.assertTrue(asked.enable_climax_hook)
        self.assertEqual(m.Options.from_dict(asked.to_dict()), asked)


if __name__ == "__main__":
    unittest.main()

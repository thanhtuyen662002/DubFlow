from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from engine.dubflow.capcut.import_pack import ImportPackError, ImportPackRequest, create_import_pack, validate_import_pack


class TimelineVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="dubflow-pack-versions-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"contract fixture, not decoded media")

    def create(self, base, cue, *, duration=None):
        timeline = {"time_base": base, "duration_ticks": duration, "cues": [dict(cue_id="c1", **cue)]}
        return create_import_pack(ImportPackRequest("timed", self.source, timeline=timeline), self.root / "pack")

    def test_millisecond_aliases_rescale_to_native_90000_base(self) -> None:
        pack = self.create({"numerator": 1, "denominator": 90000}, {"start_ms": 1000, "end_ms": 2000})
        timeline = validate_import_pack(pack.root)["timeline"]
        self.assertEqual(timeline["time_base"], {"numerator": "1", "denominator": "90000"})
        self.assertEqual(timeline["cues"][0]["start_ticks"], "90000")
        self.assertEqual(timeline["cues"][0]["end_ticks"], "180000")

    def test_non_integral_aliases_cover_interval_without_float(self) -> None:
        pack = self.create({"numerator": 1001, "denominator": 30000}, {"start_ms": 1000, "end_ms": 2000})
        cue = validate_import_pack(pack.root)["timeline"]["cues"][0]
        self.assertEqual((cue["start_ticks"], cue["end_ticks"]), ("29", "60"))

    def test_conflicting_dual_units_fail_before_publish(self) -> None:
        for cue in ({"start_ms": 1000, "start_ticks": 1000, "end_ms": 2000},
                    {"start_ms": 1000, "end_ms": 2000, "end_ticks": 2000}):
            with self.subTest(cue=cue), self.assertRaisesRegex(ImportPackError, "conflicting"):
                self.create({"numerator": 1, "denominator": 90000}, cue)
            self.assertFalse((self.root / "pack").exists())
            self.assertFalse(list(self.root.glob(".*.partial-*")))

    def test_consistent_dual_units_are_normalized(self) -> None:
        pack = self.create({"numerator": 1, "denominator": 90000},
                           {"start_ms": 1000, "start_ticks": "90000", "end_ms": 2000, "end_ticks": 180000})
        self.assertNotIn("start_ms", validate_import_pack(pack.root)["timeline"]["cues"][0])

    def test_wide_ticks_survive_json_roundtrip_and_move(self) -> None:
        start = 2**53 + 1
        pack = self.create({"numerator": "1", "denominator": "90000"},
                           {"start_ticks": str(start), "end_ticks": start + 1}, duration=2**63 - 1)
        destination = self.root / "moved folder" / "pack"
        destination.parent.mkdir()
        pack.root.rename(destination)
        timeline = validate_import_pack(destination)["timeline"]
        self.assertEqual(timeline["duration_ticks"], str(2**63 - 1))
        self.assertEqual(timeline["cues"][0]["start_ticks"], str(start))

    def test_overflow_and_non_integer_units_are_rejected(self) -> None:
        for cue in ({"start_ms": 0, "end_ms": 2**63 - 1},
                    {"start_ticks": 0, "end_ticks": 2**63},
                    {"start_ticks": False, "end_ticks": 2},
                    {"start_ms": 0.5, "end_ms": 2000}):
            with self.subTest(cue=cue), self.assertRaises(ImportPackError):
                self.create({"numerator": 1, "denominator": 90000}, cue)
            self.assertFalse((self.root / "pack").exists())

    def test_v2_missing_timeline_or_integer_wire_ticks_fail(self) -> None:
        pack = self.create({"numerator": 1, "denominator": 1000}, {"start_ticks": 0, "end_ticks": 1000})
        manifest = dict(pack.manifest)
        path = pack.root / "manifest.json"
        manifest.pop("timeline")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ImportPackError, "timeline"):
            validate_import_pack(pack.root)
        manifest["timeline"] = pack.manifest["timeline"]
        manifest["timeline"]["cues"][0]["start_ticks"] = 0
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ImportPackError, "decimal-string"):
            validate_import_pack(pack.root)

    def test_released_v1_is_read_unchanged_after_move_and_hashes_still_checked(self) -> None:
        released = Path(__file__).parent / "fixtures" / "released-v1"
        before = sha256((released / "manifest.json").read_bytes()).hexdigest()
        destination = self.root / "moved legacy pack"
        shutil.copytree(released, destination)
        manifest = validate_import_pack(destination)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertNotIn("timeline", manifest)
        self.assertEqual(sha256((destination / "manifest.json").read_bytes()).hexdigest(), before)
        (destination / manifest["assets"][0]["relative_path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ImportPackError, "asset validation"):
            validate_import_pack(destination)

    def test_legacy_caller_without_clock_emits_v1_without_invented_timeline(self) -> None:
        pack = create_import_pack(ImportPackRequest("legacy-caller", self.source), self.root / "pack")
        self.assertEqual(validate_import_pack(pack.root)["schema_version"], 1)
        self.assertNotIn("timeline", pack.manifest)


if __name__ == "__main__":
    unittest.main()

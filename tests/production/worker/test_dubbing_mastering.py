"""Mastering in Lồng tiếng (ADR-0055): off is today's job; light measures and adds bounded filters; failures fall
back to off."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.mastering.measure import Picture
from engine.dubflow.worker import production_job as worker
from tests.production.worker.test_cover import CoverFixture

COMPRESSED = Picture(16, 40, 200, 110, (0, 0, 0, 0))  # washed-out picture: black and white pulled in


class DubbingMasteringTests(unittest.TestCase):
    @contextmanager
    def job(self, picture=COMPRESSED):
        with TemporaryDirectory() as directory:
            fixture = CoverFixture(Path(directory))
            fixture.measured = []

            def frames(ffmpeg, video, times):
                fixture.measured.append(len(times))
                return [b"x"]
            with patch.object(worker, "ensure_model_profile"), \
                    patch.object(worker, "MediaProbe") as probing, \
                    patch.object(worker, "FfmpegMediaAdapter", return_value=fixture), \
                    patch.object(worker.ArgosRuntime, "prepare", autospec=True, side_effect=fixture.prepare), \
                    patch.object(worker.mastering_measure, "grey_frames", side_effect=frames), \
                    patch.object(worker.mastering_measure, "picture", return_value=picture):
                probing.return_value.probe.side_effect = fixture.probe
                yield fixture

    def options(self, fixture, value):
        fixture.config.output_dir.mkdir(parents=True, exist_ok=True)
        (fixture.config.output_dir / worker.MASTERING_FILE).write_text(json.dumps(value), encoding="utf-8")

    def test_without_mastering_json_nothing_is_measured_or_added(self):
        with self.job() as fixture:
            fixture.run()
            self.assertNotIn("mastering_filters", fixture.calls_kwargs[-1])
            self.assertNotIn("mastering", fixture.qc())
            self.assertEqual(fixture.measured, [])
            self.assertFalse((fixture.work / "mastering").exists())

    def test_off_in_the_file_is_the_same_as_no_file(self):
        with self.job() as fixture:
            self.options(fixture, {"schema_version": 1, "level": "off"})
            fixture.run()
            self.assertNotIn("mastering_filters", fixture.calls_kwargs[-1])
            self.assertEqual(fixture.measured, [])

    def test_light_grades_a_washed_out_picture_and_keeps_the_rest(self):
        with self.job() as fixture:
            self.options(fixture, {"schema_version": 1, "level": "light"})
            fixture.run()
            filters = fixture.calls_kwargs[-1]["mastering_filters"]
            self.assertEqual(len(filters), 1)
            self.assertTrue(filters[0].startswith("colorlevels="))
            for word in ("crop", "rotate", "hflip", "zoompan", "eq=", "unsharp", "atempo", "asetrate"):
                self.assertNotIn(word, filters[0])
            mastering = fixture.qc()["mastering"]
            self.assertEqual(mastering["level"], "light")
            self.assertFalse(mastering["loudness"])  # no dubbed mix in this job: the source sound is kept
            self.assertTrue(any("giữ âm thanh gốc" in note for note in mastering["notes"]))
            # Unchanged: reused; another level: measured and rendered again.
            fixture.run()
            self.assertEqual(len(fixture.calls_kwargs), 1)
            self.assertEqual(fixture.measured, [16])
            self.options(fixture, {"schema_version": 1, "level": "dynamic"})
            fixture.run()
            self.assertEqual(len(fixture.calls_kwargs), 2)

    def test_a_good_picture_gets_no_filter(self):
        with self.job(Picture(16, 16, 235, 120, (0, 0, 0, 0))) as fixture:
            self.options(fixture, {"schema_version": 1, "level": "balanced"})
            fixture.run()
            self.assertNotIn("mastering_filters", fixture.calls_kwargs[-1])
            self.assertIn("mastering", fixture.qc())

    def test_a_broken_file_fails_and_a_failed_measurement_renders_as_off(self):
        for document in ({"schema_version": 1, "level": "max"}, {"schema_version": 2, "level": "light"},
                         {"schema_version": 1, "level": "dynamic", "enable_climax_hook": "yes"}):
            with self.subTest(document=document), self.job() as fixture:
                self.options(fixture, document)
                with self.assertRaisesRegex(worker.ProductionJobError, "MASTERING_OPTIONS_INVALID"):
                    fixture.run()
        with self.job() as fixture, patch.object(worker.mastering_measure, "picture", side_effect=RuntimeError("boom")):
            self.options(fixture, {"schema_version": 1, "level": "light"})
            fixture.run()
            self.assertNotIn("mastering_filters", fixture.calls_kwargs[-1])
            self.assertTrue(any(w.startswith("MASTERING_SKIPPED") for w in fixture.qc()["warnings"]))


if __name__ == "__main__":
    unittest.main()

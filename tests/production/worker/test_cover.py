from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.media.adapter import BlurRegion, CoverStyle
from engine.dubflow.worker import production_job as worker
from tests.production.worker.test_language_routing import ProductionRouteFixture


class CoverFixture(ProductionRouteFixture):
    """The language-routing fixture, recording what each render was asked to cover."""

    def __init__(self, root):
        super().__init__(root, "zh")
        self.calls_kwargs = []

    def render(self, source, output, **kwargs):
        self.calls_kwargs.append(kwargs)
        super().render(source, output, **kwargs)

    def write_cover(self, document):
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        (self.config.output_dir / worker.COVER_FILE).write_text(json.dumps({"schema_version": 1, **document}), encoding="utf-8")

    def qc(self):
        return json.loads((self.config.output_dir / "qc_report.json").read_text(encoding="utf-8"))


DETECTED = (BlurRegion(0.2, 0.85, 0.6, 0.08, 1000, 2000), BlurRegion(0.2, 0.85, 0.6, 0.08, 3000, 4000),
            BlurRegion(0.25, 0.85, 0.5, 0.08, 5000, 6000))


class CoverTests(unittest.TestCase):
    @contextmanager
    def job(self):
        with TemporaryDirectory() as directory:
            fixture = CoverFixture(Path(directory))
            with patch.object(worker, "ensure_model_profile"), \
                    patch.object(worker, "MediaProbe") as probing, \
                    patch.object(worker, "FfmpegMediaAdapter", return_value=fixture), \
                    patch.object(worker.ArgosRuntime, "prepare", autospec=True, side_effect=fixture.prepare), \
                    patch.object(worker, "_auto_blur_regions", return_value=(DETECTED, {"regions": len(DETECTED)})):
                probing.return_value.probe.side_effect = fixture.probe
                yield fixture

    def test_without_cover_json_the_render_is_todays(self):
        with self.job() as fixture:
            fixture.config.blur_auto = True
            fixture.run()
            self.assertEqual(fixture.calls_kwargs[-1]["blur_regions"], DETECTED)
            self.assertNotIn("cover_style", fixture.calls_kwargs[-1])
            self.assertNotIn("style", fixture.qc()["caption_blur"])

    def test_a_style_alone_changes_nothing_without_regions(self):
        with self.job() as fixture:
            fixture.write_cover({"style": {"mode": "stretch", "direction": "top"}})
            fixture.run()
            self.assertEqual(fixture.calls_kwargs[-1]["blur_regions"], ())
            self.assertNotIn("cover_style", fixture.calls_kwargs[-1])

    def test_cover_json_merges_manual_fixed_and_enabled_auto_regions(self):
        with self.job() as fixture:
            fixture.config.blur_auto = True
            fixture.config.blur_regions = (BlurRegion(0.0, 0.0, 0.2, 0.1),)
            fixture.write_cover({
                "style": {"mode": "stretch", "direction": "both", "strip_px": 6},
                "regions": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1, "start_ms": 500, "end_ms": 1500}],
                "fixed": {"x": 0.7, "y": 0.02, "w": 0.25, "h": 0.06},
                # The second detection by index, the third by a time range it overlaps.
                "disabled_auto": [1, {"start_ms": 5500, "end_ms": 9000}, 40],
            })
            fixture.run()
            render = fixture.calls_kwargs[-1]
            self.assertEqual(render["cover_style"], CoverStyle("stretch", "both", 6))
            self.assertEqual(render["blur_regions"], (BlurRegion(0.0, 0.0, 0.2, 0.1), BlurRegion(0.1, 0.1, 0.3, 0.1, 500, 1500),
                                                      BlurRegion(0.7, 0.02, 0.25, 0.06), DETECTED[0]))
            self.assertEqual(fixture.qc()["caption_blur"], {"auto": {"regions": 3}, "regions": 4, "style": "stretch"})

    def test_the_style_and_regions_are_part_of_the_render_identity(self):
        with self.job() as fixture:
            fixture.config.blur_auto = True
            fixture.write_cover({"style": {"mode": "stretch"}})
            fixture.run()
            fixture.run()
            self.assertEqual(len(fixture.calls_kwargs), 1, "an unchanged cover reuses the render")
            fixture.write_cover({"style": {"mode": "mosaic"}})
            fixture.run()
            self.assertEqual(fixture.calls_kwargs[-1]["cover_style"], CoverStyle("mosaic"))
            fixture.write_cover({"style": {"mode": "mosaic"}, "disabled_auto": [0]})
            fixture.run()
            self.assertEqual(len(fixture.calls_kwargs), 3)
            self.assertEqual(fixture.calls_kwargs[-1]["blur_regions"], DETECTED[1:])

    def test_the_command_style_wins_over_the_file(self):
        with self.job() as fixture:
            fixture.config.blur_auto = True
            fixture.config.cover_style = CoverStyle("color", color="#123456")
            fixture.write_cover({"style": {"mode": "stretch"}})
            fixture.run()
            self.assertEqual(fixture.calls_kwargs[-1]["cover_style"], CoverStyle("color", color="#123456"))

    def test_a_broken_cover_json_fails_with_a_typed_error(self):
        broken = ({"style": {"mode": "smear"}}, {"regions": [{"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.1}]},
                  {"fixed": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1, "start_ms": 0, "end_ms": 10}}, {"disabled_auto": ["0"]},
                  {"disabled_auto": [{"start_ms": 10, "end_ms": 5}]}, {"regions": {}}, {"colour": "red"})
        for document in broken:
            with self.subTest(document=document), self.job() as fixture:
                fixture.write_cover(document)
                with self.assertRaisesRegex(worker.ProductionJobError, "COVER_INVALID"):
                    fixture.run()
        with self.job() as fixture:
            fixture.config.output_dir.mkdir(parents=True)
            (fixture.config.output_dir / worker.COVER_FILE).write_text('{"schema_version": 2}', encoding="utf-8")
            with self.assertRaisesRegex(worker.ProductionJobError, "COVER_INVALID"):
                fixture.run()

    def test_the_command_style_is_validated(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("app", "runtime"):
                (root / name).mkdir()
            source = root / "video.mp4"
            source.write_bytes(b"media")
            args = {"job_id": "job", "stage_id": "local-file", "source_path": str(source), "output_dir": str(root / "out"),
                    "app_root": str(root / "app"), "model_root": str(root / "models"), "media_runtime_root": str(root / "runtime"),
                    "ffmpeg_path": str(root / "runtime" / "ffmpeg.exe"), "ffprobe_path": str(root / "runtime" / "ffprobe.exe")}
            self.assertIsNone(worker.WorkerConfig.from_args(args).cover_style)
            self.assertEqual(worker.WorkerConfig.from_args({**args, "cover_style": {"mode": "stretch", "direction": "bottom"}}).cover_style,
                             CoverStyle("stretch", "bottom"))
            with self.assertRaisesRegex(worker.ProductionJobError, "COMMAND_INVALID"):
                worker.WorkerConfig.from_args({**args, "cover_style": {"mode": "stretch", "strip_px": 40}})


if __name__ == "__main__":
    unittest.main()

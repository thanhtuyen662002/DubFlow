from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import json
from types import SimpleNamespace

from engine.dubflow.subtitle.style import SubtitleStyle
from engine.dubflow.worker.production_job import (
    ASS_BAND_EVENT, SUBTITLE_STYLE_FILE, ProductionJobError, TextCue, WorkerConfig, _subtitle_style, _write_subtitles,
)

CUES = (TextCue("c1", 0, 1500, "你好", "Xin chào"), TextCue("c2", 2000, 3500, "谢谢", "Cảm ơn"))
# The exact ASS produced before the cover band existed.
LEGACY_EVENTS = [
    "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,Xin chào",
    "Dialogue: 0,0:00:02.00,0:00:03.50,Default,,0,0,0,,Cảm ơn",
]


STYLE = {"font": "Candara", "size": 1.5, "primary": "#FDE047", "outline": "#000000", "box": "#101820", "box_opacity": 0.5,
         "outline_width": 2, "bold": True, "align": "bottom"}


class SubtitleBandTests(unittest.TestCase):
    def write(self, box: bool, style: SubtitleStyle | None = None) -> tuple[str, str]:
        with TemporaryDirectory() as temp:
            srt, ass = _write_subtitles(Path(temp), CUES, box=box, style=style)
            return srt.read_text(encoding="utf-8"), ass.read_text(encoding="utf-8")

    def test_default_output_is_unchanged(self):
        srt, ass = self.write(False)
        self.assertNotIn("Band", ass)
        self.assertEqual([line for line in ass.splitlines() if line.startswith("Dialogue:")], LEGACY_EVENTS)
        self.assertIn("Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,48,1", ass)

    def test_band_covers_full_width_under_text_for_the_whole_video(self):
        srt, ass = self.write(True)
        events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(events[0], ASS_BAND_EVENT)
        self.assertIn("m 0 820 l 1920 820 1920 1064 0 1064", events[0])
        self.assertTrue(all(line.startswith("Dialogue: 1,") for line in events[1:]))
        self.assertIn("Style: Band,", ass)
        # SRT carries text only; the band is a burn-in presentation choice.
        self.assertEqual(srt, self.write(False)[0])


    def test_without_a_style_the_whole_file_is_unchanged(self):
        legacy = self.write(False)[1]
        self.assertNotIn("ScaledBorderAndShadow", legacy)
        self.assertEqual(legacy.splitlines()[:5], ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "[V4+ Styles]"])

    def test_a_chosen_style_replaces_only_the_default_style_line(self):
        srt, ass = self.write(False, SubtitleStyle.from_mapping(STYLE))
        lines = ass.splitlines()
        self.assertIn("Style: Default,Candara,72,&H0047E0FD,&H000000FF,&H00000000,&H80201810,-1,0,0,0,100,100,0,0,4,2,14,2,40,40,48,1", lines)
        self.assertIn("ScaledBorderAndShadow: yes", lines)
        self.assertEqual([line for line in lines if line.startswith("Dialogue:")], LEGACY_EVENTS)
        legacy_lines = self.write(False)[1].splitlines()
        self.assertEqual([line for line in lines if not line.startswith(("Style: Default", "ScaledBorderAndShadow"))],
                         [line for line in legacy_lines if not line.startswith("Style: Default")])
        self.assertEqual(srt, self.write(False)[0], "SRT carries text only")

    def test_a_placed_style_positions_every_line_and_keeps_text_literal(self):
        cues = (TextCue("c1", 0, 1500, "你好", "Xin {chào}"),)
        with TemporaryDirectory() as temp:
            _, ass = _write_subtitles(Path(temp), cues, box=True, style=SubtitleStyle.from_mapping(dict(STYLE, x=0.25, y=0.5)))
            events = [line for line in ass.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(events, [ASS_BAND_EVENT, "Dialogue: 1,0:00:00.00,0:00:01.50,Default,,0,0,0,,{\\an5\\pos(480,540)}Xin \\{chào\\}"])

    def test_style_comes_from_the_command_or_the_file_beside_the_outputs(self):
        with TemporaryDirectory() as temp:
            output = Path(temp)
            config = SimpleNamespace(output_dir=output, subtitle_style=None)
            self.assertIsNone(_subtitle_style(config))
            (output / SUBTITLE_STYLE_FILE).write_text(json.dumps({"schema_version": 1, "style": STYLE}), encoding="utf-8")
            self.assertEqual(_subtitle_style(config).font, "Candara")
            command = SubtitleStyle.from_mapping(dict(STYLE, font="Tahoma"))
            self.assertIs(_subtitle_style(SimpleNamespace(output_dir=output, subtitle_style=command)), command)
            for document in ({"schema_version": 2, "style": STYLE}, {"schema_version": 1, "style": dict(STYLE, font="Impact")}, []):
                (output / SUBTITLE_STYLE_FILE).write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(document=str(document)[:40]), self.assertRaises(ProductionJobError) as context:
                    _subtitle_style(config)
                self.assertEqual(context.exception.code, "SUBTITLE_STYLE_INVALID")

    def test_command_style_is_validated(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "media").mkdir()
            source = root / "clip.mp4"
            source.write_bytes(b"x")
            args = {"job_id": "j", "stage_id": "s", "source_path": str(source), "output_dir": str(root / "out"), "app_root": str(root),
                    "model_root": str(root / "models"), "media_runtime_root": str(root / "media"),
                    "ffmpeg_path": str(root / "media" / "ffmpeg.exe"), "ffprobe_path": str(root / "media" / "ffprobe.exe")}
            self.assertIsNone(WorkerConfig.from_args(args).subtitle_style)
            self.assertEqual(WorkerConfig.from_args(dict(args, subtitle_style=STYLE)).subtitle_style.size, 1.5)
            with self.assertRaises(ProductionJobError) as context:
                WorkerConfig.from_args(dict(args, subtitle_style=dict(STYLE, primary="yellow")))
            self.assertEqual(context.exception.code, "COMMAND_INVALID")


if __name__ == "__main__":
    unittest.main()

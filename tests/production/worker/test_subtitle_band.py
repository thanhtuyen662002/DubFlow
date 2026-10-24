from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.worker.production_job import ASS_BAND_EVENT, TextCue, _write_subtitles

CUES = (TextCue("c1", 0, 1500, "你好", "Xin chào"), TextCue("c2", 2000, 3500, "谢谢", "Cảm ơn"))
# The exact ASS produced before the cover band existed.
LEGACY_EVENTS = [
    "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,Xin chào",
    "Dialogue: 0,0:00:02.00,0:00:03.50,Default,,0,0,0,,Cảm ơn",
]


class SubtitleBandTests(unittest.TestCase):
    def write(self, box: bool) -> tuple[str, str]:
        with TemporaryDirectory() as temp:
            srt, ass = _write_subtitles(Path(temp), CUES, box=box)
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


if __name__ == "__main__":
    unittest.main()

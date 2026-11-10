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

    def test_a_climax_is_suggested_for_review_only_when_turned_on_and_never_rendered(self):
        import wave
        for asked in (False, True):
            with self.subTest(asked=asked), self.job() as fixture:
                fixture.work.mkdir(parents=True, exist_ok=True)
                with wave.open(str(fixture.work / "analysis.wav"), "wb") as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(8000)
                    out.writeframes(b"".join((b"\x00\x10" if 8000 * 30 <= i < 8000 * 35 else b"\x00\x00") for i in range(8000 * 50)))
                self.options(fixture, {"schema_version": 1, "level": "dynamic", **({"enable_climax_hook": True} if asked else {})})
                fixture.run()
                climax = fixture.qc()["mastering"]["climax"]
                if asked:
                    self.assertEqual((climax["start_ms"], climax["end_ms"], climax["needs_review"], climax["applied"]),
                                     (30000, 35000, True, False))
                else:
                    self.assertIsNone(climax)
                self.assertNotIn("hook", json.dumps(fixture.calls_kwargs[-1], default=str))


class VoiceTimedCaptionTests(unittest.TestCase):
    def test_captions_follow_the_tts_placement_of_their_phrase(self):
        from types import SimpleNamespace
        from engine.dubflow.asr import TimeBase, TimePoint

        ms = TimeBase(1, 1000)
        artifact = lambda segment, start, end: SimpleNamespace(segment_id=segment, source_utterance_id=segment,
                                                               slot_start=TimePoint(start, ms), actual_end=TimePoint(end, ms))
        b2 = SimpleNamespace(member_cue_ids={"phrase-1": ("cue-1", "cue-2")},
                             tts_documents=(SimpleNamespace(artifacts=(artifact("phrase-1", 1500, 4500),)),))
        cues = [worker.TextCue("cue-1", 1250, 2500, "你好", "Xin chào"), worker.TextCue("cue-2", 2601, 3103, "世界", "Thế giới"),
                worker.TextCue("cue-3", 6000, 6400, "再见", "Tạm biệt")]
        timed = worker._voice_timed(cues, b2)
        self.assertEqual([c.cue_id for c in timed], ["cue-1", "cue-2", "cue-3"])
        self.assertEqual((timed[0].start_ms, timed[1].end_ms), (1500, 4500))  # the voice, not the source speaker
        self.assertEqual((timed[2].start_ms, timed[2].end_ms), (6000, 7000))  # not dubbed: own time, at least 1 s
        self.assertEqual([c.translated_text for c in timed], ["Xin chào", "Thế giới", "Tạm biệt"])
        self.assertEqual(timed[0].source_text, "你好")

    def test_a_long_caption_is_split_and_still_written_as_srt_and_ass(self):
        from types import SimpleNamespace
        from engine.dubflow.asr import TimeBase, TimePoint

        ms = TimeBase(1, 1000)
        text = " ".join(["một câu thoại tiếng Việt rất dài"] * 6)
        b2 = SimpleNamespace(member_cue_ids={"p": ("cue-1",)}, tts_documents=(SimpleNamespace(artifacts=(
            SimpleNamespace(segment_id="p", source_utterance_id="cue-1", slot_start=TimePoint(0, ms), actual_end=TimePoint(9000, ms)),)),))
        timed = worker._voice_timed([worker.TextCue("cue-1", 0, 3000, "长句", text)], b2)
        self.assertGreater(len(timed), 1)
        self.assertTrue(all(len(line) <= 42 for c in timed for line in c.translated_text.split("\n")))
        with TemporaryDirectory() as folder:
            srt, ass = worker._write_subtitles(Path(folder), timed)
            self.assertIn("rất dài", srt.read_text(encoding="utf-8"))
            self.assertTrue(ass.is_file())


if __name__ == "__main__":
    unittest.main()

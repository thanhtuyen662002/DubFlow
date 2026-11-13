"""Mastering in Lồng tiếng (ADR-0055): off is today's job; light measures and adds bounded filters; failures fall
back to off."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace as dataclass_replace
import json
import os
from pathlib import Path
import shutil
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

    def test_a_picture_filter_the_media_runtime_refuses_is_dropped_and_not_tried_again(self):
        """rc1 Windows check: a failing mastering picture filter failed the job (the B1 fallback reused it) and the
        saved plan kept failing every later run. Now the video renders without it and the plan forgets it."""
        from engine.dubflow.media.adapter import MediaAdapterError

        with self.job() as fixture:
            render = fixture.render

            def refusing(source, output, **kwargs):
                if kwargs.get("mastering_filters"):
                    fixture.calls_kwargs.append(kwargs)
                    raise MediaAdapterError("MEDIA_COMMAND_FAILED", "filter refused")
                render(source, output, **kwargs)
            fixture.render = refusing
            self.options(fixture, {"schema_version": 1, "level": "light"})
            fixture.run()
            self.assertIn("mastering_filters", fixture.calls_kwargs[0])
            self.assertNotIn("mastering_filters", fixture.calls_kwargs[-1])
            qc = fixture.qc()
            self.assertTrue(any(w.startswith("MASTERING_VIDEO_SKIPPED: MEDIA_COMMAND_FAILED") for w in qc["warnings"]))
            self.assertEqual(qc["mastering"]["video_filters"], [])
            self.assertTrue(any("không render được" in note for note in qc["mastering"]["notes"]))
            plan = json.loads((fixture.work / "mastering" / "dubbing-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["video_filters"], [])
            calls = len(fixture.calls_kwargs)
            fixture.run()  # unchanged: the finished video is reused, the broken filter is not tried again
            self.assertEqual(len(fixture.calls_kwargs), calls)

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


class VoicedEndTests(unittest.TestCase):
    """rc1 Windows check: a short sentence padded with silence to its slot kept its caption up 0.2–2.6 s after the
    voice stopped. The caption now leaves with the voice."""

    @staticmethod
    def _wav(path: Path, voiced_ms: int, silent_ms: int, rate: int = 24000) -> None:
        import math
        import struct
        import wave

        samples = [int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(rate * voiced_ms // 1000)]
        samples += [0] * (rate * silent_ms // 1000)
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    def test_the_caption_ends_where_the_voice_stops_not_at_the_padding(self):
        from types import SimpleNamespace
        from engine.dubflow.asr import TimeBase, TimePoint

        ms = TimeBase(1, 1000)
        with TemporaryDirectory() as folder:
            padded, full = Path(folder) / "padded.wav", Path(folder) / "full.wav"
            self._wav(padded, 1500, 2500)
            self._wav(full, 2000, 0)
            self.assertEqual(worker._voiced_end_ms(padded, 1000, 5000), 1000 + 1500 + worker.VOICED_TAIL_MS)
            self.assertEqual(worker._voiced_end_ms(full, 0, 2000), 2000, "never past the piece's own end")
            self.assertEqual(worker._voiced_end_ms(Path(folder) / "missing.wav", 0, 2000), 2000)
            self.assertEqual(worker._voiced_end_ms(None, 0, 2000), 2000)
            silent = Path(folder) / "silent.wav"
            self._wav(silent, 0, 1000)
            self.assertEqual(worker._voiced_end_ms(silent, 0, 1000), 1000)
            artifact = SimpleNamespace(segment_id="p", source_utterance_id="cue-1", slot_start=TimePoint(1000, ms),
                                       actual_end=TimePoint(5000, ms), path=str(padded))
            b2 = SimpleNamespace(member_cue_ids={"p": ("cue-1",)}, tts_documents=(SimpleNamespace(artifacts=(artifact,)),))
            timed = worker._voice_timed([worker.TextCue("cue-1", 900, 5000, "你好", "Xin chào các bạn")], b2)
            self.assertEqual((timed[0].start_ms, timed[0].end_ms), (1000, 2700))


class VoiceTimedFallbackTests(unittest.TestCase):
    """Owner review of #692: when the dubbed render fails output QC the job falls back to the source sound (B1);
    captions timed by the Vietnamese voice must go back to the source times with it."""

    def test_a_fallback_to_the_source_sound_restores_source_timed_captions(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from engine.dubflow.asr import TimeBase, TimePoint
        from tests.production.worker.test_separation import SeparationWorkerTests

        helper = SeparationWorkerTests()
        ms = TimeBase(1, 1000)

        def b2(**kwargs):
            result = helper.fake_b2(**kwargs)
            artifact = SimpleNamespace(segment_id="phrase-a", source_utterance_id="a", slot_start=TimePoint(1500, ms),
                                       actual_end=TimePoint(2900, ms))
            result.tts_documents = (SimpleNamespace(artifacts=(artifact,), failures=(), warnings=(),
                                                    provenance=result.tts_document.provenance),)
            result.member_cue_ids = {"phrase-a": ("a",)}
            return result

        with helper.fixture() as (root, config, probe, media), helper.pipeline(probe, media) as (dub, _):
            dub.side_effect = b2
            config = dataclass_replace(config, separate_background=False)
            config.output_dir.mkdir(parents=True, exist_ok=True)
            (config.output_dir / worker.MASTERING_FILE).write_text(json.dumps({"schema_version": 1, "level": "balanced"}),
                                                                   encoding="utf-8")
            seen = []
            media.render.side_effect = lambda source, target, **kw: (
                seen.append((Path(kw["subtitle_path"]).with_suffix(".srt").read_text(encoding="utf-8"), kw.get("audio_path"))),
                target.write_bytes(b"rendered video seam"))
            failures = [worker.ProductionJobError("QC_AUDIO_CODEC", "forced")]

            def validate(source_probe, candidate):
                if failures:
                    raise failures.pop()
            final = worker.mastering_measure.Loudness(-20.5, -3.1, 4.2, -30.6, 0.0)
            with patch.object(worker.mastering_measure, "loudness",
                              side_effect=lambda ffmpeg, path: final if Path(path).name == "final_vi.mp4" else None), \
                    patch.object(worker.mastering_measure, "grey_frames", return_value=[]), \
                    patch.object(worker, "_validate_rendered_audio", side_effect=validate), \
                    patch.object(worker, "_separate_background_stage", return_value=(None, {"mode": "original"})):
                result = worker.run_local_file(config, Mock())
            self.assertEqual(len(seen), 2)
            dubbed, fallback = seen
            self.assertIn("00:00:01,500 --> 00:00:02,900", dubbed[0])  # the voice's time while the voice is heard
            self.assertIsNotNone(dubbed[1])
            self.assertIsNone(fallback[1])
            self.assertIn("00:00:00,000 --> 00:00:01,000", fallback[0])  # the source time once only the source is heard
            self.assertNotIn("00:00:01,500", fallback[0])
            qc = json.loads(Path(result["qc"]).read_text(encoding="utf-8"))
            self.assertTrue(any(w.startswith("B2_AUDIO_FALLBACK_TO_B1") for w in qc["warnings"]))
            # what mastering said about the dubbed mix no longer holds once only the source is heard (run 9)
            notes = qc["mastering"]["notes"]
            self.assertFalse(any(note.startswith(("Âm lượng:", "Phụ đề:")) for note in notes), notes)
            self.assertTrue(any("B1" in note for note in notes))
            self.assertFalse(qc["mastering"]["loudness"])  # nothing normalised: the dubbed mix is not in the output
            # issue #715: deliberately changed from "None". The B1 fallback's actual final MP4 is still measured after AAC.
            self.assertEqual(qc["mastering"]["output_loudness"],
                             {"integrated": -20.5, "true_peak": -3.1, "lra": 4.2, "threshold": -30.6, "offset": 0.0})


class PostAacMeasurementTests(unittest.TestCase):
    """Issue #715: with mastering on, the final MP4 is measured after AAC even when no normalisation filter was
    needed; "loudness" still means a filter was applied; a failed measurement is None and never fails the job."""

    def run_job(self, level, measure):
        from unittest.mock import Mock
        from tests.production.worker.test_separation import SeparationWorkerTests

        helper = SeparationWorkerTests()
        calls = []

        def loudness(ffmpeg, path):
            calls.append(Path(path).name)
            return measure(Path(path))
        with helper.fixture() as (root, config, probe, media), helper.pipeline(probe, media):
            config = dataclass_replace(config, separate_background=False)
            config.output_dir.mkdir(parents=True, exist_ok=True)
            if level is not None:
                (config.output_dir / worker.MASTERING_FILE).write_text(json.dumps({"schema_version": 1, "level": level}),
                                                                       encoding="utf-8")
            with patch.object(worker.mastering_measure, "loudness", side_effect=loudness), \
                    patch.object(worker.mastering_measure, "grey_frames", return_value=[]), \
                    patch.object(worker, "_separate_background_stage", return_value=(None, {"mode": "original"})):
                result = worker.run_local_file(config, Mock())
            work_plans = list(Path(root).rglob("dubbing-plan.json"))
            plan = json.loads(work_plans[0].read_text(encoding="utf-8")) if work_plans else None
            return json.loads(Path(result["qc"]).read_text(encoding="utf-8")), calls, plan

    def test_b2_with_no_filter_needed_is_still_measured_after_aac(self):
        after = worker.mastering_measure.Loudness(-16.4, -2.2, 5.0, -26.5, 0.1)
        qc, calls, plan = self.run_job("balanced", lambda path: after if path.name == "final_vi.mp4" else None)
        self.assertIsNone(plan["audio_filter"], "the mix needed no filter")
        self.assertEqual(qc["audio"]["mode"], "dubbed")  # B2: the dubbed mix is in the output
        self.assertFalse(qc["mastering"]["loudness"], "no normalisation was applied")
        self.assertEqual(qc["mastering"]["output_loudness"],
                         {"integrated": -16.4, "true_peak": -2.2, "lra": 5.0, "threshold": -26.5, "offset": 0.1})
        self.assertEqual(calls.count("final_vi.mp4"), 1)

    def test_a_failed_post_aac_measurement_is_none_and_the_job_passes(self):
        def failing(path):
            if path.name == "final_vi.mp4":
                raise worker.mastering_measure.MeasureError("ebur128 failed")
            return None
        qc, calls, _ = self.run_job("light", failing)
        self.assertEqual(qc["status"], "passed")
        self.assertIsNone(qc["mastering"]["output_loudness"])
        self.assertIn("final_vi.mp4", calls)

    def test_mastering_off_measures_nothing_and_has_no_card(self):
        qc, calls, plan = self.run_job(None, lambda path: None)
        self.assertNotIn("mastering", qc)
        self.assertEqual(calls, [])
        self.assertIsNone(plan)


Loud = worker.mastering_measure.Loudness
RC17_MIX = Loud(-15.42, -0.73, 7.60, -26.04, 0.77)  # issue #821 rc17: planned to −17.2 LUFS, −2.51 dBTP before AAC
CLOSE_MIX = Loud(-16.2, -3.0, 6.0, -26.3, 0.1)       # within 1 LU and under −2.5 dBTP: the planner adds no filter
DYNAMIC_WORDS = ("alimiter", "compand", "acompressor", "dynaudnorm", "agate", "asoftclip", "linear=false")


class AacJob:
    """One Lồng tiếng job (the separation fixture, B2 with a real mix file) with its FFmpeg calls recorded.

    ``trials``: what each AAC trial measures, in order (a dBTP number, None for silence or an exception to raise);
    ``final``: what the final MP4 measures (a Loudness, None or an exception). Trial encodes and mastered WAVs are
    "written" by the recorded ``_capture`` so hashes and renames behave as on disk."""

    def __init__(self, level, mix, trials=(), final=None, *, b2_error=None, render_error=False, root=None):
        self.level, self.mix, self.trials, self.final = level, mix, list(trials), final
        self.b2_error, self.render_error, self.root = b2_error, render_error, root
        self.captures, self.measured, self.renders, self.rendered_audio = [], [], [], []

    def capture(self, argv):
        self.captures.append(list(argv))
        out = Path(argv[-1])
        if "pcm_s16le" in argv:
            out.write_bytes(b"RIFF-mastered:" + argv[argv.index("-af") + 1].encode())
        else:
            out.write_bytes(b"aac trial of " + Path(argv[argv.index("-i") + 1]).read_bytes()[:64])
        return b"", b""

    def loudness(self, ffmpeg, path):
        name = Path(path).name
        self.measured.append(name)
        if name == "final.wav":
            return self.mix
        if name == "aac_trial.m4a":
            if not Path(path).is_file():
                raise AssertionError("the trial was measured before it was encoded")
            value = self.trials.pop(0)
            if isinstance(value, Exception):
                raise value
            return None if value is None else Loud(-17.5, value, 7.6, -27.6, 0.1)
        if name == "final_vi.mp4":
            if isinstance(self.final, Exception):
                raise self.final
            return self.final
        raise AssertionError(f"unexpected measurement of {name}")

    @contextmanager
    def running(self):
        from unittest.mock import Mock
        from tests.production.worker.test_separation import SeparationWorkerTests

        helper = SeparationWorkerTests()
        with helper.fixture() as (root, config, probe, media), helper.pipeline(probe, media) as (dub, _):
            self.root, self.config, self.media = Path(root), dataclass_replace(config, separate_background=False), media
            if self.b2_error is not None:
                dub.side_effect = self.b2_error
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            if self.level is not None:
                (self.config.output_dir / worker.MASTERING_FILE).write_text(
                    json.dumps({"schema_version": 1, "level": self.level}), encoding="utf-8")

            def render(source, target, **kwargs):
                self.renders.append(kwargs)
                audio = kwargs.get("audio_path")
                self.rendered_audio.append(Path(audio).read_bytes() if audio is not None else None)
                if self.render_error and kwargs.get("audio_path") is not None:
                    raise worker.MediaAdapterError("MEDIA_COMMAND_FAILED", "mix refused")
                target.write_bytes(b"rendered video seam")
            media.render.side_effect = render
            with patch.object(worker.mastering_measure, "loudness", side_effect=self.loudness), \
                    patch.object(worker.mastering_measure, "_capture", side_effect=self.capture), \
                    patch.object(worker.mastering_measure, "grey_frames", return_value=[]), \
                    patch.object(worker, "_separate_background_stage", return_value=(None, {"mode": "original"})):
                yield lambda: worker.run_local_file(self.config, Mock())

    def run(self):
        with self.running() as run:
            return self.result(run())

    def result(self, done):
        self.qc = json.loads(Path(done["qc"]).read_text(encoding="utf-8"))
        final = self.config.output_dir / "final_vi.mp4"
        self.published = final.read_bytes() if final.is_file() else None
        plans = list(self.root.rglob("dubbing-plan.json"))
        self.plan = json.loads(plans[0].read_text(encoding="utf-8")) if plans else None
        self.trial_encodes = [argv for argv in self.captures if "aac" in argv]
        folders = [path for path in self.root.rglob("mastering") if path.is_dir()]
        # what the mastering folder holds besides the plan and the mastered mix: the trial must leave nothing behind
        self.leftovers = sorted(path.name for folder in folders for path in folder.iterdir()
                                if path.name not in ("dubbing-plan.json", "final_mix_mastered.wav"))
        self.applies = [argv for argv in self.captures if "pcm_s16le" in argv]
        return self

    @property
    def conformance(self):
        return self.qc["mastering"]["conformance"]


class AacTrueCeilingTests(unittest.TestCase):
    """Issue #821: the B2 export's true peak is checked against −1.5 dBTP on the encoded AAC, not predicted; a linear
    trim (never a limiter) brings it under; what could not be met or measured is a warning, never a silent pass."""

    def assert_linear(self, audio_filter):
        for word in DYNAMIC_WORDS:
            self.assertNotIn(word, audio_filter or "")

    def assert_no_trial_leftovers(self, job):
        self.assertEqual(job.leftovers, [])

    def test_b2_loudnorm_within_the_ceiling_after_aac_needs_no_trim(self):
        job = AacJob("light", RC17_MIX, trials=[-2.6], final=Loud(-17.22, -2.6, 7.4, -27.4, 0.1)).run()
        self.assertEqual(job.plan["recipe"], "dubbing-master-v4")
        self.assertTrue(job.plan["audio_filter"].startswith("loudnorm=I=-17.2:TP=-1.5:"))
        self.assertNotIn("volume=", job.plan["audio_filter"])
        self.assertEqual(job.plan["aac_trial"], {"first_true_peak": -2.6, "true_peak": -2.6, "trim_db": 0.0, "tries": 1})
        # the trial encodes the WAV the render uses, with exactly the render's audio arguments
        (trial,) = job.trial_encodes
        mastered = job.renders[-1]["audio_path"]
        self.assertEqual(Path(mastered).name, "final_mix_mastered.wav")
        self.assertEqual(trial[trial.index("-i") + 1], str(mastered))
        self.assertEqual(trial[trial.index("-map"):trial.index("-f")], ["-map", "0:a:0", *worker.AAC_RENDER_ARGS])
        self.assertEqual(len(job.applies), 1)
        self.assertEqual(job.conformance, {"status": "passed", "ceiling_dbtp": -1.5, "true_peak_dbtp": -2.6,
                                           "path": "B2", "trim_db": 0.0, "tries": 1})
        self.assertEqual(job.qc["mastering"]["result"],
                         {"technical_render": "success", "mastering_conformance": "passed", "final_qc": "passed"})
        self.assertEqual((job.qc["status"], job.qc["downgrade"]), ("passed", False))
        self.assertFalse(any(w.startswith("MASTERING_") for w in job.qc["warnings"]))
        self.assertEqual(job.measured.count("final_vi.mp4"), 1)
        self.assert_no_trial_leftovers(job)

    def test_b2_post_aac_overshoot_rc17_is_trimmed_linearly_then_passes(self):
        # rc17: −2.51 dBTP before AAC, −1.31 after. Trim (−1.31 + 1.5) + 0.3 = 0.49 dB, then the trial passes.
        job = AacJob("light", RC17_MIX, trials=[-1.31, -2.29], final=Loud(-17.71, -2.29, 7.4, -27.9, 0.1)).run()
        audio_filter = job.plan["audio_filter"]
        self.assertTrue(audio_filter.startswith("loudnorm=I=-17.2:TP=-1.5:LRA=11:"), audio_filter)
        self.assertTrue(audio_filter.endswith(",aresample=48000,volume=-0.49dB"), audio_filter)
        self.assertIn("linear=true", audio_filter)
        self.assert_linear(audio_filter)
        self.assertEqual(job.plan["aac_trial"], {"first_true_peak": -1.31, "true_peak": -2.29, "trim_db": 0.49, "tries": 2})
        # the trimmed mix is made again from the dubbed mix (PCM), never from decoded AAC
        self.assertEqual([Path(argv[argv.index("-i") + 1]).name for argv in job.applies], ["final.wav", "final.wav"])
        self.assertEqual([argv[argv.index("-af") + 1] for argv in job.applies][-1], audio_filter)
        self.assertEqual(Path(job.renders[-1]["audio_path"]).name, "final_mix_mastered.wav")
        self.assertEqual(job.rendered_audio[-1], b"RIFF-mastered:" + audio_filter.encode())
        self.assertEqual(job.conformance["status"], "corrected")
        self.assertEqual((job.conformance["trim_db"], job.conformance["tries"], job.conformance["true_peak_dbtp"]),
                         (0.49, 2, -2.29))
        self.assertTrue(any(note == "Âm lượng sau AAC: giảm thêm 0,5 dB để đỉnh ≤ −1,5 dBTP." for note in job.qc["mastering"]["notes"]))
        self.assertTrue(job.qc["mastering"]["loudness"])
        self.assertEqual((job.qc["status"], job.qc["downgrade"]), ("passed", False))
        self.assertEqual(job.qc["mastering"]["result"]["final_qc"], "passed")
        self.assert_no_trial_leftovers(job)

    def test_b2_without_a_planner_filter_gets_a_volume_only_trim(self):
        job = AacJob("light", CLOSE_MIX, trials=[-1.2, -2.0], final=Loud(-16.8, -2.0, 6.0, -26.9, 0.1)).run()
        self.assertEqual(job.plan["audio_filter"], "volume=-0.60dB")
        self.assertEqual([Path(argv[argv.index("-i") + 1]).name for argv in job.trial_encodes],
                         ["final.wav", "final_mix_trim1.wav"])  # the first trial is the dubbed mix the render would use
        self.assertEqual(Path(job.renders[-1]["audio_path"]).name, "final_mix_mastered.wav")
        self.assertTrue(job.qc["mastering"]["loudness"], "a trim is a gain applied to the dubbed mix")
        self.assertEqual(job.conformance["status"], "corrected")
        self.assertEqual(job.conformance["trim_db"], 0.6)
        # within the ceiling at the first trial: nothing applied, the final mix itself is rendered, measured once
        job = AacJob("light", CLOSE_MIX, trials=[-2.2], final=Loud(-16.2, -2.2, 6.0, -26.3, 0.1)).run()
        self.assertIsNone(job.plan["audio_filter"])
        self.assertEqual(job.applies, [])
        self.assertEqual(Path(job.renders[-1]["audio_path"]).name, "final.wav")
        self.assertEqual(job.conformance["status"], "passed")
        self.assertFalse(job.qc["mastering"]["loudness"])
        self.assertEqual(job.measured.count("final_vi.mp4"), 1)

    def test_b2_with_a_high_input_peak_stays_linear(self):
        loud = Loud(-11.8, 0.6, 9.2, -22.0, 0.3)
        job = AacJob("light", loud, trials=[-0.9, -1.95], final=Loud(-19.6, -1.95, 9.0, -29.8, 0.1)).run()
        audio_filter = job.plan["audio_filter"]
        planned = worker.dubbing_master.plan(worker.MasteringOptions("light"), mix=loud, mix_rate=48000, frames=None,
                                             width=1920, height=1080).audio_filter
        self.assertEqual(audio_filter, planned + ",volume=-0.90dB")  # the planner's own filter (target, LRA) unchanged
        self.assertIn(":LRA=11:", audio_filter)
        self.assert_linear(audio_filter)
        self.assertEqual(job.conformance["status"], "corrected")

    def test_b2_near_the_ceiling_exactly_at_it_passes_and_just_over_is_trimmed(self):
        job = AacJob("light", RC17_MIX, trials=[-1.5], final=Loud(-17.2, -1.5, 7.4, -27.4, 0.1)).run()
        self.assertNotIn("volume=", job.plan["audio_filter"])
        self.assertEqual(job.conformance["status"], "passed")
        job = AacJob("light", RC17_MIX, trials=[-1.49, -1.82], final=Loud(-17.5, -1.82, 7.4, -27.7, 0.1)).run()
        self.assertTrue(job.plan["audio_filter"].endswith("volume=-0.31dB"))
        self.assertEqual(job.conformance["status"], "corrected")

    def test_a_non_monotonic_overshoot_is_measured_again_after_every_trim(self):
        # rc17: more gain reduction does not always lower the AAC peak; every trim is cumulative and re-measured.
        job = AacJob("light", RC17_MIX, trials=[-1.31, -1.40, -1.20, -1.62],
                     final=Loud(-18.7, -1.62, 7.4, -28.9, 0.1)).run()
        trims = [argv[argv.index("-af") + 1].rsplit(",", 1)[-1] for argv in job.applies[1:]]
        self.assertEqual(trims, ["volume=-0.49dB", "volume=-0.89dB", "volume=-1.49dB"])
        self.assertEqual(len(job.trial_encodes), 4)
        self.assertEqual(job.plan["aac_trial"], {"first_true_peak": -1.31, "true_peak": -1.62, "trim_db": 1.49, "tries": 4})
        self.assertEqual(job.conformance["status"], "corrected")
        self.assert_no_trial_leftovers(job)

    def test_exhausted_tries_keep_the_lowest_peak_and_report_failed_with_a_warning(self):
        job = AacJob("light", RC17_MIX, trials=[-1.31, -1.40, -1.20, -1.35],
                     final=Loud(-17.7, -1.40, 7.4, -27.9, 0.1)).run()
        self.assertEqual(len(job.trial_encodes), 1 + worker.dubbing_master.AAC_TRIM_TRIES, "bounded")
        self.assertTrue(job.plan["audio_filter"].endswith("volume=-0.49dB"), "the attempt with the lowest peak")
        self.assertEqual(job.rendered_audio[-1], b"RIFF-mastered:" + job.plan["audio_filter"].encode())
        self.assertEqual(job.plan["aac_trial"], {"first_true_peak": -1.31, "true_peak": -1.40, "trim_db": 0.49, "tries": 4})
        self.assertEqual(job.conformance["status"], "failed")
        self.assertEqual(job.conformance["true_peak_dbtp"], -1.40)
        over = [w for w in job.qc["warnings"] if w.startswith("MASTERING_TRUE_PEAK_OVER:")]
        self.assertEqual(len(over), 1)
        self.assertIn("-1.40 dBTP > -1.5 dBTP", over[0])
        self.assertTrue(job.qc["downgrade"], "the supervisor then reports completed_with_warnings")
        self.assertEqual(job.qc["status"], "passed", "the report stays valid: the render succeeded")
        self.assertEqual(job.qc["mastering"]["result"],
                         {"technical_render": "success", "mastering_conformance": "failed", "final_qc": "warning"})
        self.assertEqual(job.published, b"rendered video seam", "the render is still produced and published")
        self.assertTrue(any(note.startswith("Âm lượng sau AAC:") and "chưa đạt trần" in note
                            for note in job.qc["mastering"]["notes"]))
        self.assert_no_trial_leftovers(job)

    def test_the_b1_fallback_is_measured_and_warned_but_never_trimmed(self):
        from engine.dubflow.worker.b2_audio import B2AudioError

        job = AacJob("light", RC17_MIX, final=Loud(-14.0, -0.4, 8.0, -24.0, 0.1),
                     b2_error=B2AudioError("TTS_FAILED", "forced")).run()
        self.assertEqual(job.captures, [], "no trial, no filter: the source sound is not touched")
        self.assertNotIn("aac_trial.m4a", job.measured)
        self.assertIsNone(job.renders[-1].get("audio_path"))
        self.assertTrue(job.renders[-1]["preserve_original_audio"])
        self.assertEqual(job.conformance, {"status": "not_applicable", "ceiling_dbtp": -1.5, "true_peak_dbtp": -0.4,
                                           "path": "B1", "trim_db": 0.0, "tries": 0, "ceiling_met": False})
        over = [w for w in job.qc["warnings"] if w.startswith("MASTERING_TRUE_PEAK_OVER:")]
        self.assertEqual(len(over), 1)
        self.assertIn("(B1, âm thanh gốc, không chỉnh)", over[0])
        self.assertEqual(job.qc["audio"]["mode"], "original")
        # a dubbed render that fails falls back to B1 after a trim: the trim is not reported for the source sound
        job = AacJob("light", RC17_MIX, trials=[-1.31, -2.29], final=Loud(-14.0, -2.4, 8.0, -24.0, 0.1),
                     render_error=True).run()
        self.assertTrue(job.renders[-1]["preserve_original_audio"])
        self.assertEqual((job.conformance["status"], job.conformance["path"], job.conformance["ceiling_met"],
                          job.conformance["trim_db"]), ("not_applicable", "B1", True, 0.0))
        self.assertFalse(any(note.startswith("Âm lượng") for note in job.qc["mastering"]["notes"]))
        self.assertFalse(any(w.startswith("MASTERING_TRUE_PEAK") for w in job.qc["warnings"]))
        # the B1 export could not be measured: said in the record and the warnings, never read as a pass
        job = AacJob("light", RC17_MIX, final=worker.mastering_measure.MeasureError("FFmpeg failed (1)"),
                     b2_error=B2AudioError("TTS_FAILED", "forced")).run()
        self.assertEqual(job.conformance, {"status": "not_applicable", "ceiling_dbtp": -1.5, "true_peak_dbtp": None,
                                           "path": "B1", "trim_db": 0.0, "tries": 0, "ceiling_met": None,
                                           "unmeasured": True})
        unmeasured = [w for w in job.qc["warnings"] if w.startswith("MASTERING_TRUE_PEAK_UNMEASURED:")]
        self.assertEqual(len(unmeasured), 1)
        self.assertIn("(B1, âm thanh gốc, không chỉnh)", unmeasured[0])
        self.assertEqual(job.qc["mastering"]["result"]["final_qc"], "warning")

    def test_a_vietsub_only_job_is_not_applicable_and_not_warned(self):
        from types import SimpleNamespace

        with DubbingMasteringTests().job() as fixture, \
                patch.object(worker.mastering_measure, "loudness", return_value=Loud(-14.0, -0.4, 8.0, -24.0, 0.1)), \
                patch.object(worker.mastering_measure, "_capture", side_effect=AssertionError("nothing to encode")):
            DubbingMasteringTests.options(SimpleNamespace(), fixture, {"schema_version": 1, "level": "light"})
            fixture.run()
            qc = fixture.qc()
        self.assertEqual(qc["mastering"]["conformance"]["status"], "not_applicable")
        self.assertEqual(qc["mastering"]["conformance"]["path"], "none")
        self.assertFalse(any(w.startswith("MASTERING_TRUE_PEAK") for w in qc["warnings"]))

    def test_failed_measurements_are_unmeasured_with_a_warning_and_never_fail_the_job(self):
        boom = worker.mastering_measure.MeasureError("FFmpeg failed (1)")
        job = AacJob("light", RC17_MIX, trials=[boom], final=boom).run()
        self.assertFalse(any(w.startswith("MASTERING_SKIPPED") for w in job.qc["warnings"]), "mastering stays on")
        self.assertIsNone(job.plan["aac_trial"])
        self.assertNotIn("volume=", job.plan["audio_filter"], "the planner's filter, as before the trial")
        self.assertEqual(Path(job.renders[-1]["audio_path"]).name, "final_mix_mastered.wav")
        self.assertEqual(job.conformance["status"], "unmeasured")
        self.assertIsNone(job.conformance["true_peak_dbtp"])
        self.assertTrue(any(w.startswith("MASTERING_TRUE_PEAK_UNMEASURED:") for w in job.qc["warnings"]))
        self.assertEqual((job.qc["status"], job.qc["downgrade"]), ("passed", True))
        self.assertEqual(job.qc["mastering"]["result"]["final_qc"], "warning")
        self.assert_no_trial_leftovers(job)
        # the trial alone failing: the export is still measured, and a measured pass is a pass
        job = AacJob("light", RC17_MIX, trials=[boom], final=Loud(-17.2, -2.3, 7.4, -27.4, 0.1)).run()
        self.assertEqual(job.conformance["status"], "passed")
        self.assertFalse(job.qc["downgrade"])
        # a trim whose encode fails keeps the planner's mix (never a half-made one)
        job = AacJob("light", RC17_MIX, trials=[-1.31, boom], final=Loud(-17.2, -1.31, 7.4, -27.4, 0.1)).run()
        self.assertIsNone(job.plan["aac_trial"])
        self.assertNotIn("volume=", job.plan["audio_filter"])
        self.assertEqual(job.rendered_audio[-1], b"RIFF-mastered:" + job.plan["audio_filter"].encode())
        self.assertEqual(job.conformance["status"], "failed")
        self.assert_no_trial_leftovers(job)

    def test_resume_reuses_the_trial_result_without_encoding_again(self):
        job = AacJob("light", RC17_MIX, trials=[-1.31, -2.29], final=Loud(-17.71, -2.29, 7.4, -27.9, 0.1))
        with job.running() as run:
            first = job.result(run())
            plan, conformance, captures = first.plan, first.conformance, len(job.captures)
            renders, measured = len(job.renders), len(job.measured)
            again = job.result(run())  # cancelled after the render and resumed, or simply run again
        self.assertEqual(len(job.captures), captures, "no new trial encode or apply")
        self.assertEqual(len(job.renders), renders, "the finished render is reused")
        self.assertEqual(job.measured[measured:], ["final_vi.mp4"], "only the export is measured again")
        self.assertEqual(again.plan, plan)
        self.assertEqual(again.conformance, conformance)
        self.assertEqual(again.conformance["status"], "corrected")

    def test_a_trial_that_could_not_run_is_tried_again_on_the_next_run(self):
        # a one-off failure (a timeout, a WAV briefly locked) must not leave the untrimmed plan cached for good
        boom = worker.mastering_measure.MeasureError("FFmpeg timed out")
        job = AacJob("light", RC17_MIX, trials=[boom, -1.31, -2.29], final=Loud(-17.71, -2.29, 7.4, -27.9, 0.1))
        with job.running() as run:
            first = job.result(run())
            self.assertIsNone(first.plan["aac_trial"])
            self.assertNotIn("volume=", first.plan["audio_filter"])
            renders = len(job.renders)
            again = job.result(run())
            captures = len(job.captures)
            job.result(run())  # once the trial has run, the plan is reused as before: no third trial
        self.assertEqual(len(job.captures), captures)
        self.assertEqual(again.plan["aac_trial"], {"first_true_peak": -1.31, "true_peak": -2.29, "trim_db": 0.49, "tries": 2})
        self.assertTrue(again.plan["audio_filter"].endswith("volume=-0.49dB"))
        self.assertEqual(len(job.renders), renders + 1, "the plan changed, so the export is rendered again")
        self.assertEqual(job.rendered_audio[-1], b"RIFF-mastered:" + again.plan["audio_filter"].encode())
        self.assertEqual(again.conformance["status"], "corrected")
        self.assertEqual(job.trials, [])
        self.assert_no_trial_leftovers(again)

    def test_a_half_written_trimmed_mix_is_removed_and_the_planner_mix_kept(self):
        class KilledTrim(AacJob):
            def capture(self, argv):
                if "pcm_s16le" in argv and "volume=" in argv[argv.index("-af") + 1]:
                    self.captures.append(list(argv))
                    Path(argv[-1]).write_bytes(b"half a trimmed mix")  # the .partial.wav, then the encode dies
                    raise worker.mastering_measure.MeasureError("FFmpeg failed (1)")
                return super().capture(argv)

        job = KilledTrim("light", RC17_MIX, trials=[-1.31], final=Loud(-17.2, -1.31, 7.4, -27.4, 0.1)).run()
        self.assertTrue(any(Path(argv[-1]).name == "final_mix_trim1.partial.wav" for argv in job.applies))
        self.assert_no_trial_leftovers(job)
        self.assertIsNone(job.plan["aac_trial"])
        self.assertNotIn("volume=", job.plan["audio_filter"])
        self.assertEqual(job.rendered_audio[-1], b"RIFF-mastered:" + job.plan["audio_filter"].encode())
        self.assertFalse(any(w.startswith("MASTERING_SKIPPED") for w in job.qc["warnings"]))

    def test_a_late_editable_link_fallback_updates_the_mastering_result(self):
        job = AacJob("light", RC17_MIX, trials=[-1.31, -2.29], final=Loud(-17.71, -2.29, 7.4, -27.9, 0.1))
        with patch.object(worker.os, "link", side_effect=OSError("exFAT")):
            job.run()
        self.assertTrue(any(w.startswith("EDITABLE_LINK_FALLBACK:") for w in job.qc["warnings"]))
        self.assertTrue(job.qc["downgrade"])
        self.assertEqual(job.qc["status"], "passed")
        self.assertEqual(job.qc["mastering"]["result"],
                         {"technical_render": "success", "mastering_conformance": "corrected", "final_qc": "warning"})

    def test_a_render_from_unchanged_input_gets_the_same_filter_and_trim(self):
        runs = [AacJob("light", RC17_MIX, trials=[-1.31, -1.40, -1.20, -1.62], final=Loud(-18.7, -1.62, 7.4, -28.9, 0.1)).run()
                for _ in range(2)]
        self.assertEqual(runs[0].plan, runs[1].plan)
        self.assertEqual([argv[argv.index("-af") + 1] for argv in runs[0].applies],
                         [argv[argv.index("-af") + 1] for argv in runs[1].applies])
        self.assertEqual(runs[0].conformance, runs[1].conformance)

    def test_mastering_off_runs_no_trial_and_its_report_is_unchanged(self):
        job = AacJob(None, RC17_MIX, trials=[-1.31], final=Loud(-17.0, -1.0, 7.0, -27.0, 0.1)).run()
        self.assertEqual((job.captures, job.measured), ([], []))
        self.assertIsNone(job.plan)
        self.assertEqual(Path(job.renders[-1]["audio_path"]).name, "final.wav")
        self.assertNotIn("mastering_filters", job.renders[-1])
        from engine.dubflow.tts import approved_default_voice

        voice = approved_default_voice()
        expected = {**OFF_QC, "audio": {**OFF_QC["audio"], "voice_hash": voice.content_hash(), "voice_id": voice.voice_id,
                                        "voice_version": voice.voice_version}}
        self.assertEqual(_normalised(job.qc, job.root), expected)

    def test_the_trial_encodes_with_exactly_the_render_audio_arguments(self):
        import sys
        from engine.dubflow.media import FfmpegMediaAdapter
        from tests.production.media.test_adapter import RecordingRunner

        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, audio = root / "source.mp4", root / "mix.wav"
            source.write_bytes(b"source")
            audio.write_bytes(b"audio")
            runner = RecordingRunner()
            executable = Path(sys.executable).resolve()
            adapter = FfmpegMediaAdapter(executable, trusted_root=executable.parent, runner=runner)
            adapter.render(source, root / "out.mp4", audio_path=audio, preserve_original_audio=False, overwrite=True)
            call = list(runner.calls[-1])
        at = call.index("-c:a")
        self.assertEqual(tuple(call[at:at + len(worker.AAC_RENDER_ARGS)]), worker.AAC_RENDER_ARGS)
        self.assertEqual(call.count("-c:a"), 1)
        self.assertNotIn("-af", call, "the dubbed mix reaches the AAC encoder unfiltered, as in the trial")


def _normalised(qc, root):
    """The report without its time and with paths under the job folder reduced to their file name."""
    def walk(value):
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str) and value.startswith(str(root)):
            return "<job>/" + Path(value).name
        return value
    return walk({key: value for key, value in qc.items() if key != "validated_at_utc"})


# Mastering off (issue #821, ADR-0055 decision 2): the report of an off job as it was before the AAC conformance check.
_OFF_PROBE = {
    "canonical_duration_ticks": 3000, "canonical_time_base": {"denominator": 1000, "numerator": 1},
    "format_duration_ticks": None, "format_name": None, "format_time_base": {"denominator": 1000, "numerator": 1},
    "has_audio": True, "source_path": "<job>/source.mp4",
    "streams": [{"channels": None, "codec_name": "h264", "codec_type": "video", "duration_ticks": 3000, "duration_ts": 3000,
                 "frame_rate": None, "height": None, "index": 0, "sample_rate": None, "start_pts": 0,
                 "time_base": {"denominator": 1000, "numerator": 1}, "width": None},
                {"channels": 2, "codec_name": "aac", "codec_type": "audio", "duration_ticks": 144000, "duration_ts": 144000,
                 "frame_rate": None, "height": None, "index": 1, "sample_rate": 48000, "start_pts": 0,
                 "time_base": {"denominator": 48000, "numerator": 1}, "width": None}],
    "video_timeline": {"duration_ticks": 3000, "end_pts": 3000, "start_pts": 0,
                       "time_base": {"denominator": 1000, "numerator": 1}}}
# Recorded from the job at 74c16d0 (before #821). The voice fields come from the approved default voice pack.
OFF_QC = {
    "schema_version": 1, "status": "passed", "source_probe": _OFF_PROBE, "output_probe": _OFF_PROBE,
    "audio": {"backend": "hermetic-tts", "bed": "source", "mix_document": "<job>/mix_document.json", "mix_failures": 0,
              "mix_provenance": {}, "mix_warnings": [], "mode": "dubbed", "source_audio_origin": "decoded-source",
              "tts_document": "<job>/tts_document.json", "tts_failures": 0,
              "undubbed": {"duration_ms": 0, "phrases": 0, "speech_ms": 1000, "starts_ms": []},
              "voice_hash": "<voice>", "voice_id": "<voice>", "voice_version": "<voice>"},
    "background": {"mode": "original"}, "downgrade": False, "warnings": []}


class AacConformanceUnitTests(unittest.TestCase):
    def test_the_output_measurement_tells_silence_from_an_error(self):
        from types import SimpleNamespace
        config = SimpleNamespace(ffmpeg_path=Path("ffmpeg"))
        with patch.object(worker.mastering_measure, "loudness", return_value=None):
            self.assertEqual(worker._output_measurement(config, Path("final_vi.mp4")), (None, None))
        with patch.object(worker.mastering_measure, "loudness", side_effect=worker.mastering_measure.MeasureError("x")):
            found, error = worker._output_measurement(config, Path("final_vi.mp4"))
            self.assertIsNone(found)
            self.assertEqual(error, "MeasureError: x")

    def test_conformance_states(self):
        loud = lambda peak: {"integrated": -17.0, "true_peak": peak, "lra": 7.0, "threshold": -27.0, "offset": 0.1}
        trial = {"first_true_peak": -1.31, "true_peak": -2.29, "trim_db": 0.49, "tries": 2}
        cases = [
            (("B2", None, loud(-2.0), None), "passed", []),
            (("B2", trial, loud(-2.29), None), "corrected", []),
            (("B2", trial, loud(-1.2), None), "failed", ["MASTERING_TRUE_PEAK_OVER"]),
            (("B2", None, loud(-1.2), None), "failed", ["MASTERING_TRUE_PEAK_OVER"]),  # no trial ran: still judged
            (("B2", trial, None, "MeasureError: x"), "unmeasured", ["MASTERING_TRUE_PEAK_UNMEASURED"]),
            (("B2", trial, None, None), "silent", []),
            (("B1", None, loud(-0.4), None), "not_applicable", ["MASTERING_TRUE_PEAK_OVER"]),
            (("B1", None, loud(-3.0), None), "not_applicable", []),
            (("none", None, loud(-0.4), None), "not_applicable", []),
            (("B1", None, None, "MeasureError: x"), "not_applicable", ["MASTERING_TRUE_PEAK_UNMEASURED"]),
            (("none", None, None, "MeasureError: x"), "not_applicable", []),  # Vietsub only: recorded, not warned
        ]
        for arguments, status, codes in cases:
            with self.subTest(arguments=arguments):
                warnings = []
                record = worker._mastering_conformance(*arguments, warnings)
                self.assertEqual(record["status"], status)
                self.assertEqual(record["ceiling_dbtp"], -1.5)
                self.assertEqual([w.split(":")[0] for w in warnings], codes)
                self.assertFalse(any(w.startswith(worker.INFORMATIONAL_WARNINGS) for w in warnings))
                if arguments[0] != "B2":
                    self.assertEqual(record.get("unmeasured", False), arguments[3] is not None)
                    if arguments[3] is not None:
                        self.assertIsNone(record["ceiling_met"], "an unmeasured export is never a pass")

    def test_an_over_warning_says_what_the_trial_did(self):
        def warning(trial):
            warnings = []
            worker._mastering_conformance("B2", trial, {"true_peak": -1.31}, None, warnings)
            (only,) = warnings
            return only
        self.assertTrue(warning({"trim_db": 0.49, "tries": 4}).endswith("(trimmed 0.49 dB over 4 trial encodes)"))
        # all tries used and the untrimmed attempt measured lowest: the trims were tried and discarded, not "0 dB"
        kept = warning({"first_true_peak": -1.31, "true_peak": -1.31, "trim_db": 0.0, "tries": 4})
        self.assertTrue(kept.endswith("(no trim lowered the peak over 4 trial encodes; kept untrimmed)"), kept)
        self.assertNotIn("trimmed 0.00", kept)
        self.assertTrue(warning(None).endswith("(no AAC trial result)"))


FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "set DUBFLOW_TEST_FFMPEG (or put ffmpeg on PATH) to run against the real encoder")
class RealAacTrimTests(unittest.TestCase):
    """Issue #821 with the real encoder: a mix at −2.5 dBTP before AAC that peaks more than 1 dB higher after it is
    trimmed by the real trial/trim helper until the encoded file itself measures ≤ −1.5 dBTP."""

    @staticmethod
    def write(path, frames, gain=1.0):
        import struct
        import wave
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(2)
            writer.setsampwidth(2)
            writer.setframerate(48000)
            writer.writeframes(b"".join(struct.pack("<hh", round(a * gain), round(b * gain)) for a, b in frames))

    def mix(self, folder):
        """40 ms bursts of hard-clipped noise every 400 ms (square-like edges the AAC encode rings over), scaled to
        −2.5 dBTP: about −14.5 LUFS, and about +3 dB of peak after AAC on FFmpeg 6.1.1 and n9.0.2."""
        import random
        rng = random.Random(821)
        clip = lambda value: max(-1.0, min(1.0, value))
        frames = [(clip(rng.gauss(0, 3)) * 20000, clip(rng.gauss(0, 3)) * 20000) if i % 19200 < 1920 else (0.0, 0.0)
                  for i in range(48000 * 6)]
        raw = folder / "raw.wav"
        self.write(raw, frames)
        peak = worker.mastering_measure.loudness(Path(FFMPEG), raw).true_peak
        mix = folder / "final.wav"
        self.write(mix, frames, 10 ** ((-2.5 - peak) / 20))
        return mix

    def encoded_peak(self, wav, folder):
        """The true peak of a separate encode with the render's AAC arguments (not the helper's own trial)."""
        import subprocess
        out = folder / "check.m4a"
        subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(wav), "-map", "0:a:0",
                        *worker.AAC_RENDER_ARGS, str(out)], check=True, capture_output=True, timeout=300)
        return worker.mastering_measure.loudness(Path(FFMPEG), out).true_peak

    def test_the_trial_and_trim_bring_the_encoded_peak_under_the_ceiling(self):
        ffmpeg = Path(FFMPEG)
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            mix = self.mix(folder)
            before = worker.mastering_measure.loudness(ffmpeg, mix)
            self.assertAlmostEqual(before.true_peak, -2.5, delta=0.15)
            untrimmed = self.encoded_peak(mix, folder)
            self.assertGreater(untrimmed - before.true_peak, 1.0, "the signal must overshoot after AAC by more than 1 dB")
            (folder / "mastering").mkdir()
            # no planner filter: the trim alone; then the planner's own linear loudnorm (−1.5 LU here) and the trim
            planned = worker.dubbing_master.plan(worker.MasteringOptions("light"), mix=before, mix_rate=48000,
                                                 frames=None, width=1920, height=1080).audio_filter
            self.assertIn("linear=true", planned)
            for base in (None, planned):
                with self.subTest(base=base):
                    mastered = folder / "mastering" / "final_mix_mastered.wav"
                    if base:
                        worker._apply_audio_filter(ffmpeg, mix, base, mastered)
                    audio_filter, record = worker._aac_conform(ffmpeg, mix, base, mastered)
                    self.assertGreater(record["first_true_peak"], -1.5)
                    self.assertGreater(record["trim_db"], 0)
                    self.assertLessEqual(record["true_peak"], -1.5)
                    self.assertLessEqual(record["tries"], 1 + worker.dubbing_master.AAC_TRIM_TRIES)
                    self.assertTrue(audio_filter.endswith(f"volume=-{record['trim_db']:.2f}dB"))
                    self.assertLessEqual(self.encoded_peak(mastered, folder), -1.5)
                    self.assertEqual(sorted(path.name for path in (folder / "mastering").iterdir()),
                                     ["final_mix_mastered.wav"])
                    mastered.unlink()


class OutputLoudnessTests(unittest.TestCase):
    """QC states what the viewer hears: the final MP4 audio after AAC (Phase 5 Hậu kỳ card)."""

    def test_the_final_video_is_measured_and_a_failed_measurement_is_none(self):
        from types import SimpleNamespace
        config = SimpleNamespace(ffmpeg_path=Path("ffmpeg"))
        found = worker.mastering_measure.Loudness(-16.2, -2.4, 6.1, -26.3, 0.2)
        with patch.object(worker.mastering_measure, "loudness", return_value=found) as measure:
            self.assertEqual(worker._output_loudness(config, Path("final_vi.mp4")),
                             {"integrated": -16.2, "true_peak": -2.4, "lra": 6.1, "threshold": -26.3, "offset": 0.2})
        measure.assert_called_once_with(Path("ffmpeg"), Path("final_vi.mp4"))
        with patch.object(worker.mastering_measure, "loudness", side_effect=worker.mastering_measure.MeasureError("x")):
            self.assertIsNone(worker._output_loudness(config, Path("final_vi.mp4")))
        with patch.object(worker.mastering_measure, "loudness", return_value=None):
            self.assertIsNone(worker._output_loudness(config, Path("final_vi.mp4")))

    def test_a_malformed_loudnorm_block_is_none_and_never_fails_the_export(self):
        """Issue #715: the post-AAC measurement now runs on every mastered export; a malformed print must not fail it."""
        from types import SimpleNamespace
        config = SimpleNamespace(ffmpeg_path=Path("ffmpeg"))
        real = worker.mastering_measure.loudness
        broken = b'[Parsed_loudnorm_0 @ 0] \n{"input_i" : "-16.1", "input_tp" : -2.0,, }\n'
        with patch.object(worker.mastering_measure, "loudness",
                          side_effect=lambda ffmpeg, video: real(ffmpeg, video, capture=lambda argv: (b"", broken))):
            self.assertIsNone(worker._output_loudness(config, Path("final_vi.mp4")))


if __name__ == "__main__":
    unittest.main()

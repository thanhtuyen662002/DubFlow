"""Issue #321: shorten speech that does not fit, and bound windows by measured silence."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.media import Rational
from engine.dubflow.media.adapter import MediaStream
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice
from engine.dubflow.tts.adapter import FAST_FIT_MAX_MILLI, TtsError
from engine.dubflow.worker import b2_audio
from engine.dubflow.worker.b2_audio import run_b2_audio
from engine.dubflow.worker.production_job import TextCue, _undubbed_summary
from engine.dubflow.worker.tts_rewrites import plan_rewrite, validate_spoken_text_document

ROOT = Path(__file__).resolve().parents[3]
SECONDS = 40
VIDEO_STREAM = MediaStream(0, "video", "h264", Rational(1, 1000), 0, SECONDS * 1000, SECONDS * 1000)
# Each is refused today: too long after the regular rewrite, even at 1.5x.
TOO_LONG = ("À, bởi vì tôi thật sự mệt.", "Này, tất cả mọi người nghe đây!", "Nếu như anh vẫn còn ở đây.",
            "Chị ấy đã từng nói như vậy.", "Ngày hôm nay trời rất là đẹp.", "Tôi biết rồi, tôi đi ngay lập tức.",
            "Dạ, tuy nhiên em chưa hiểu.", "Bọn họ đến ngay lập tức rồi.", "Đợi một chút, bản thân mình sẽ làm.",
            "Ừ, một lần nữa thì sao nào.")
FITS = ("Xin chào.", "Đi thôi.")
LIMIT = 24  # Characters the fixture voice can speak in its slot at 1.5x.


def write_wav(path: Path, frames: bytes, *, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as writer:
        writer.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(frames)
    return path


class TextLengthEngine(DeterministicFixtureEngine):
    """A voice whose speech outgrows a short slot by text length alone."""

    def __init__(self, requests):
        super().__init__()
        self.requests = requests

    def synthesize(self, request):
        self.requests.append(request)
        if len(request.segment.text) > LIMIT or (len(request.segment.text) > LIMIT * 2 // 3
                                                 and request.config.max_speed_ratio_milli < FAST_FIT_MAX_MILLI):
            raise TtsError("DURATION_FIT_REQUIRED", "speech is longer than its slot")
        return super().synthesize(request)


class SilentSourceAdapter:
    ffmpeg_path = Path("unused-fixture-ffmpeg")

    def __init__(self, frames: bytes | None = None):
        self.frames = frames

    def extract_audio(self, _source, output, **options):
        rate = options["sample_rate"]
        return write_wav(output, self.frames if self.frames is not None else struct.pack("<hh", 900, 900) * rate * SECONDS, rate=rate)


class CondensedFitTests(unittest.TestCase):
    def run_job(self, root: Path, texts, requests, **options):
        cues = tuple(TextCue(f"cue-{index}", index * 3000, index * 3000 + 1000, "原文", text) for index, text in enumerate(texts))
        with patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
                   return_value=(SimpleNamespace(sample_rate=16000, channels=1), approved_default_voice())), \
             patch("engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", side_effect=lambda *a, **kw: TextLengthEngine(requests)):
            return run_b2_audio(media=options.pop("media", SilentSourceAdapter()), source_path=root / "source.mp4",
                source_probe=SimpleNamespace(has_audio=True, video=VIDEO_STREAM), translated_cues=cues, source_language="zh",
                app_root=ROOT, profile_path=ROOT / "models/manifests/production-cpu-v1.json", work_dir=root / "b2", **options)

    def test_condensed_tier_fits_phrases_refused_today_without_touching_subtitles(self):
        for text in TOO_LONG:
            regular = plan_rewrite(text)
            self.assertGreater(len(regular.spoken_text if regular else text), LIMIT, text)
            self.assertLessEqual(len(plan_rewrite(text, condensed=True).spoken_text), LIMIT, text)
        requests = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_job(root, TOO_LONG + FITS, requests)
            self.assertEqual(result.tts_document.failures, ())
            self.assertEqual(len(result.tts_document.artifacts), len(TOO_LONG + FITS))
            spoken = json.loads(result.spoken_text_document_path.read_bytes())
            entries = {entry["original_input"]["segment_id"]: entry for entry in spoken["entries"]}
            for index, text in enumerate(TOO_LONG):
                entry = entries[f"cue-{index}"]
                self.assertEqual(entry["original_input"]["text"], text)  # Subtitle text is unchanged.
                self.assertEqual(entry["spoken_text"], plan_rewrite(text, condensed=True).spoken_text)
                self.assertEqual(entry["mode"], "rewritten")
                self.assertLessEqual(entry["fit"]["speed_ratio_milli"], FAST_FIT_MAX_MILLI)
                attempts = [r for r in requests if r.segment.segment_id == f"cue-{index}"]
                self.assertLessEqual(len(attempts), 5)  # At most one regular and one condensed rewrite.
                self.assertLessEqual(len({r.segment.text for r in attempts} - {text}), 2)
                self.assertEqual(attempts[-1].segment.text, entry["spoken_text"])
            for index in range(len(TOO_LONG), len(TOO_LONG + FITS)):
                self.assertEqual(entries[f"cue-{index}"]["mode"], "original")  # A fitting phrase does not regress.
                self.assertEqual(len([r for r in requests if r.segment.segment_id == f"cue-{index}"]), 1)
            originals = {}
            for request in requests:
                originals.setdefault(request.segment.segment_id, request.segment)
            segments = list(originals.values())
            validate_spoken_text_document(spoken, result.tts_document_path.read_bytes(), segments,
                identity=spoken["checkpoint_identity"], member_cue_ids={key: list(value) for key, value in result.member_cue_ids.items()})
            requests.clear()
            resumed = self.run_job(root, TOO_LONG + FITS, requests)
            self.assertEqual(requests, [])  # Condensed receipts are reusable checkpoints.
            self.assertEqual(resumed.tts_document.artifacts, result.tts_document.artifacts)

    def test_a_phrase_still_too_long_is_refused_once_with_evidence(self):
        requests = []
        with TemporaryDirectory() as directory:
            result = self.run_job(Path(directory), ("Vì vậy chúng ta phải đi ngay bây giờ thôi nhé.", "Xin chào."), requests)
            self.assertEqual([failure.segment_id for failure in result.tts_document.failures], ["cue-0"])
            refused = [warning for warning in result.tts_document.warnings if warning.startswith("TTS_DURATION_FIT_REFUSED: cue-0")]
            self.assertTrue(refused and refused[0].endswith(f"max speed {FAST_FIT_MAX_MILLI}"), result.tts_document.warnings)
            self.assertLessEqual(len([r for r in requests if r.segment.segment_id == "cue-0"]), 5)


class MeasuredSilenceTests(unittest.TestCase):
    def track(self, rate, spans):
        """Stereo PCM: the given (start_ms, end_ms, value) runs over silence."""
        values = [0] * (rate * SECONDS)
        for start, end, value in spans:
            values[start * rate // 1000:end * rate // 1000] = [value] * ((end - start) * rate // 1000)
        return b"".join(struct.pack("<hh", value, value) for value in values)

    def test_resume_detection_skips_the_cue_tail_and_stops_before_new_speech(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            rate = 16000
            music = [(0, SECONDS * 1000, 3000)]
            # Speech: a 200 ms tail after cue a; unrecognized speech at 2600 ms; silence after b.
            speech = [(0, 1200, 9000), (2600, 3000, 9000), (5000, 6000, 9000)]
            source = write_wav(root / "source.wav", self.track(rate, music + [(s, e, v + 3000) for s, e, v in speech]), rate=rate)
            background = write_wav(root / "background.wav", self.track(rate, music), rate=rate)
            resumes = b2_audio._speech_resumes_ms(source, background, [("a", 1000, 5000), ("b", 6000, 12000), ("c", 600, 1100)])
            self.assertEqual(resumes["a"], 2600)
            self.assertEqual(resumes["b"], 12000)  # Silent throughout.
            self.assertEqual(resumes["c"], 600)  # Speech never pauses: the window stays at the cue.
            windows = b2_audio._dubbing_windows(
                [{"cue_id": "a", "start_ms": 0, "end_ms": 1000, "confidence": 1.0},
                 {"cue_id": "b", "start_ms": 5000, "end_ms": 6000, "confidence": 1.0},
                 {"cue_id": "z", "start_ms": 30000, "end_ms": 31000, "confidence": 1.0}], SECONDS * 1000)
            measured, limits = b2_audio._measured_windows(windows, source, background, SECONDS * 1000)
            ends = {window["cue_id"]: int(window["render_window_end"]["ticks"]) for window in measured}
            self.assertEqual((ends["a"], limits["a"]), (2480, 2480))  # Guard before the resumed speech.
            self.assertEqual((ends["b"], limits["b"]), (8000, 12000))  # Measured silence beyond the 4 s fallback.
            self.assertEqual(limits["z"], 37000)

    def test_separated_job_records_measured_gap_evidence(self):
        requests = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            background = write_wav(root / "background.wav", struct.pack("<hh", 900, 900) * 16000 * SECONDS)
            result = CondensedFitTests().run_job(root, ("Xin chào.", "Đi thôi."), requests, background_path=background)
            placement = json.loads(result.placement_document_path.read_bytes())
            self.assertEqual(placement["gap_evidence"], b2_audio.MEASURED_GAP_EVIDENCE)
            self.assertEqual(result.gap_evidence, b2_audio.MEASURED_GAP_EVIDENCE)
            unseparated = CondensedFitTests().run_job(root / "plain", ("Xin chào.",), [])
            self.assertEqual(unseparated.gap_evidence, b2_audio.UNMEASURED_GAP_EVIDENCE)


class UndubbedSummaryTests(unittest.TestCase):
    def test_summary_counts_whole_phrases_once(self):
        cues = (TextCue("a", 0, 1000, "x", "y"), TextCue("b", 1100, 2000, "x", "y"), TextCue("c", 840_000, 842_000, "x", "y"))
        summary = _undubbed_summary(cues, {"phrase-a": ("a", "b")}, ("phrase-a", "c", "phrase-a"))
        self.assertEqual(summary, {"phrases": 2, "duration_ms": 4000, "speech_ms": 3900, "starts_ms": [0, 840_000]})
        self.assertEqual(_undubbed_summary(cues, {}, ()), {"phrases": 0, "duration_ms": 0, "speech_ms": 3900, "starts_ms": []})


if __name__ == "__main__":
    unittest.main()

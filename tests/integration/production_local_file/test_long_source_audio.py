"""A source longer than one 48 kHz stereo WAV (6 h 12 min) is still dubbed.

Customer report 2026-11-10: a 6+ hour video came back with Vietnamese
subtitles and no voice, because the decoded 48 kHz stereo source passed the
4 GiB WAV bound and the mixer refused it after every phrase was synthesised.
"""
from __future__ import annotations

import io
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.media import Rational
from engine.dubflow.media.adapter import MediaStream
from engine.dubflow.mix import FileSegment, FileSource, MixConfig, ResourceProfile, StreamLimits, StreamingAudioMixer
from engine.dubflow.mix.streaming import file_hash
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice
from engine.dubflow.worker import b2_audio
from engine.dubflow.worker.b2_audio import B2AudioError, run_b2_audio, source_pcm_format, _source_duration_ms
from engine.dubflow.worker.production_job import TextCue

ROOT = Path(__file__).resolve().parents[3]
HOUR_MS = 3_600_000
VIDEO_STREAM = MediaStream(0, "video", "h264", Rational(1, 1000), 0, 3000, 3000)


def wav_bytes(rate: int, channels: int, frames: int, value: int = 0) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(struct.pack("<" + "h" * channels, *([value] * channels)) * frames)
    return stream.getvalue()


class RecordingDecoder:
    """Writes 3 s of PCM in whatever format the caller asks for."""
    ffmpeg_path = Path("unused-fixture-ffmpeg")

    def __init__(self):
        self.requests = []

    def extract_audio(self, _source, output: Path, **options):
        self.requests.append((options["sample_rate"], options["channels"]))
        output.write_bytes(wav_bytes(options["sample_rate"], options["channels"], options["sample_rate"] * 3, 900))
        return output


def long_probe(hours: float):
    # The picture is long; the hermetic decoder writes only 3 s, which the
    # media extent clamps to, so the test stays small.
    return SimpleNamespace(has_audio=True, video=VIDEO_STREAM, duration_ticks=int(hours * HOUR_MS),
                           format_duration_ticks=None, format_time_base=None, audio=())


class SourcePcmFormatTests(unittest.TestCase):
    def test_short_sources_keep_48k_stereo(self):
        for duration in (None, 0, 60_000, 5 * HOUR_MS):
            self.assertEqual(source_pcm_format(duration, 48_000), (48_000, 2))

    def test_long_sources_step_down_and_always_fit_one_wav(self):
        limit = StreamLimits().max_source_bytes
        expected = {6.5: (32_000, 2), 10: (24_000, 2), 15: (16_000, 2), 30: (16_000, 1)}
        for hours, shape in expected.items():
            with self.subTest(hours=hours):
                rate, channels = source_pcm_format(int(hours * HOUR_MS), 48_000)
                self.assertEqual((rate, channels), shape)
                self.assertLess(int(hours * 3600 * 1.05) * rate * channels * 2, limit)
        # The exact customer case: just over 6 h no longer picks 48 kHz stereo.
        self.assertNotEqual(source_pcm_format(int(6.2 * HOUR_MS), 48_000), (48_000, 2))

    def test_a_source_beyond_every_format_is_refused_before_synthesis(self):
        with self.assertRaisesRegex(B2AudioError, "SOURCE_AUDIO_TOO_LONG"):
            source_pcm_format(40 * HOUR_MS, 48_000)

    def test_longest_probed_duration_counts(self):
        probe = SimpleNamespace(duration_ticks=1000, format_duration_ticks=7 * 3600, format_time_base=Rational(1, 1),
                                audio=(SimpleNamespace(duration_ticks=2 * HOUR_MS, time_base=Rational(1, 1000)),))
        self.assertEqual(_source_duration_ms(probe), 7 * HOUR_MS)
        self.assertIsNone(_source_duration_ms(SimpleNamespace()))


class LongSourceDubbingTests(unittest.TestCase):
    def run_job(self, root: Path, decoder, hours: float):
        with patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
                   return_value=(SimpleNamespace(sample_rate=48_000, channels=1), approved_default_voice())), patch(
                "engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine", return_value=DeterministicFixtureEngine()):
            return run_b2_audio(media=decoder, source_path=root / "long.mp4", source_probe=long_probe(hours),
                source_language="zh", app_root=ROOT, profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                work_dir=root / "b2", translated_cues=(TextCue("cue-1", 500, 1500, "你好", "Xin chào", 0.95),))

    def test_seven_hour_source_is_dubbed_at_a_reduced_rate(self):
        with TemporaryDirectory() as directory:
            decoder = RecordingDecoder()
            result = self.run_job(Path(directory), decoder, 7)
            self.assertEqual(decoder.requests, [(32_000, 2)])
            self.assertFalse(result.mix_document.failures)
            with wave.open(str(result.final_mix_path), "rb") as reader:
                self.assertEqual((reader.getframerate(), reader.getnchannels()), (32_000, 2))
                self.assertTrue(any(reader.readframes(reader.getnframes())))
            with wave.open(str(result.dialogue_stem_path), "rb") as reader:
                self.assertTrue(any(reader.readframes(reader.getnframes())), "the voice is in the mix")
            self.assertTrue(any(warning.startswith("SOURCE_AUDIO_FORMAT_REDUCED:") for warning in result.device_warnings))

    def test_short_source_is_unchanged(self):
        with TemporaryDirectory() as directory:
            decoder = RecordingDecoder()
            result = self.run_job(Path(directory), decoder, 1)
            self.assertEqual(decoder.requests, [(48_000, 2)])
            self.assertFalse(any(warning.startswith("SOURCE_AUDIO_FORMAT_REDUCED:") for warning in result.device_warnings))

    def test_audio_longer_than_the_probe_is_decoded_again_smaller_before_synthesis(self):
        with TemporaryDirectory() as directory:
            decoder = RecordingDecoder()
            fits = iter((False, True))
            with patch.object(b2_audio, "_decoded_wav_fits", side_effect=lambda _path: next(fits)), \
                    patch.object(Path, "stat", autospec=True, side_effect=self._huge_source_stat()):
                result = self.run_job(Path(directory), decoder, 1)
            self.assertEqual(decoder.requests[0], (48_000, 2))
            self.assertEqual(len(decoder.requests), 2)
            self.assertLess(decoder.requests[1][0] * decoder.requests[1][1], 48_000 * 2)
            self.assertFalse(result.mix_document.failures)

    @staticmethod
    def _huge_source_stat():
        real = Path.stat

        def stat(path, *args, **kwargs):
            value = real(path, *args, **kwargs)
            if path.name == "source_audio.wav" and not hasattr(stat, "seen"):
                stat.seen = True  # The first decode measured 7 h of 48 kHz stereo.
                return SimpleNamespace(st_size=7 * 3600 * 48_000 * 4 + 44, st_mode=value.st_mode)
            return value
        return stat


class AveragedDecimationTests(unittest.TestCase):
    def test_speech_above_the_source_rate_is_averaged_not_aliased(self):
        base = TimeBase(1, 1000)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.wav"; source.write_bytes(wav_bytes(1000, 2, 1000, 0))
            # Alternating samples at 2 kHz: pure Nyquist energy that vanishes at 1 kHz.
            speech = root / "speech.wav"
            with wave.open(str(speech), "wb") as writer:
                writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(2000)
                writer.writeframes(struct.pack("<hh", 8000, -8000) * 200)
            segment = FileSegment("s", "u", TimePoint(100, base), TimePoint(300, base), speech, file_hash(speech))
            config = MixConfig(resource=ResourceProfile(max_memory_mb=512))
            limits = StreamLimits(block_frames=127, disk_reserve_bytes=0)
            stems = {}
            for average in (False, True):
                document = StreamingAudioMixer(output_dir=root / str(average), config=config, limits=limits,
                    average_decimation=average).mix(FileSource(source, TimePoint(0, base), TimePoint(1000, base)),
                    [segment], input_hash="sha256:" + "1" * 64)
                with wave.open(document.dialogue_stem.path, "rb") as reader:
                    stems[average] = reader.readframes(reader.getnframes())
            self.assertTrue(any(stems[False]), "nearest-sample decimation keeps the aliased tone")
            self.assertFalse(any(stems[True]), "averaging cancels energy the source rate cannot carry")


if __name__ == "__main__":
    unittest.main()

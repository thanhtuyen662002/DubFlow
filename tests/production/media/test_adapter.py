from __future__ import annotations

import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.media import (
    BlurRegion,
    FfmpegMediaAdapter,
    MediaAdapterError,
    MediaProbe,
    MediaTimeline,
    Rational,
    parse_ffprobe_json,
)
from engine.dubflow.media.adapter import (
    MAX_BLUR_REGIONS, CoverStyle, MediaStream, audio_origin_samples, blur_filtergraph, cover_filtergraph, video_bitrate,
)


def _probe_document() -> dict[str, object]:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 640,
                "height": 360,
                "time_base": "1/12800",
                "start_pts": -12,
                "duration_ts": 512,
                "r_frame_rate": "25/1",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "time_base": "1/48000",
                "start_pts": 0,
                "duration": "0.040000",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "time_base": "1/1000",
            "duration": "0.040000",
        },
    }


class RecordingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail

    def __call__(self, argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "decoder failed token=should-not-leak")
        if "-show_streams" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(_probe_document()), "")
        Path(argv[-1]).write_bytes(b"real-media-command-output")
        return subprocess.CompletedProcess(argv, 0, "", "")


class MediaProbeParsingTests(unittest.TestCase):
    def test_parse_preserves_integer_timebase_pts_and_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            result = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source.resolve())

        self.assertEqual(result.video.time_base, Rational(1, 12800))
        self.assertEqual(result.video.start_pts, -12)
        self.assertEqual(result.video.duration_ticks, 512)
        self.assertEqual(result.video.frame_rate, Rational(25, 1))
        self.assertTrue(result.has_audio)
        self.assertEqual(result.audio[0].sample_rate, 48000)
        self.assertEqual(result.audio[0].duration_ticks, 1920)
        self.assertEqual(result.duration_ticks, 40)
        serialized = result.to_dict()
        self.assertEqual(serialized["canonical_duration_ticks"], 40)
        self.assertEqual(serialized["video_timeline"]["start_pts"], -1)
        self.assertEqual(serialized["video_timeline"]["duration_ticks"], 40)
        self.assertEqual(serialized["video_timeline"]["end_pts"], 39)
        timeline = result.video_timeline()
        self.assertEqual(timeline.start_pts, -12)
        self.assertEqual(timeline.duration_ticks, 512)
        self.assertEqual(timeline.end_pts, 500)
        self.assertEqual(result.video_timeline(Rational(1, 25600)).start_pts, -24)
        self.assertEqual(result.video_timeline(Rational(1, 25600)).duration_ticks, 1024)

    def test_non_integral_rescale_requires_named_rounding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            result = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source.resolve())
        with self.assertRaisesRegex(MediaAdapterError, "MEDIA_TIMELINE_NON_INTEGRAL"):
            result.video_timeline(Rational(1, 1000))
        rounded = result.video_timeline(Rational(1, 1000), start_rounding="floor")
        self.assertEqual(rounded.start_pts, -1)
        self.assertEqual(rounded.duration_ticks, 40)
        self.assertEqual(rounded.end_pts, 39)

    def test_decimal_duration_rounds_up_without_binary_float(self) -> None:
        document = _probe_document()
        streams = document["streams"]
        assert isinstance(streams, list)
        streams[0].pop("duration_ts")
        streams[0]["duration"] = "0.0400001"
        with tempfile.TemporaryDirectory() as directory:
            result = parse_ffprobe_json(json.dumps(document), source_path=(Path(directory) / "x.mp4").resolve())
        self.assertEqual(result.video.duration_ticks, 513)

    def test_format_decimal_duration_without_ffprobe_timebase_uses_canonical_base(self) -> None:
        document = _probe_document()
        streams = document["streams"]
        assert isinstance(streams, list)
        streams[0].pop("duration_ts")
        format_document = document["format"]
        assert isinstance(format_document, dict)
        format_document.pop("time_base")
        format_document["duration"] = "0.0400001"
        with tempfile.TemporaryDirectory() as directory:
            result = parse_ffprobe_json(json.dumps(document), source_path=(Path(directory) / "x.mp4").resolve())
        self.assertEqual(result.format_time_base, Rational(1, 1000))
        self.assertEqual(result.format_duration_ticks, 41)
        self.assertEqual(result.duration_ticks, 41)

    def test_invalid_json_and_missing_video_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = (Path(directory) / "x.mp4").resolve()
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_METADATA_INVALID"):
                parse_ffprobe_json("{broken", source_path=source)
            no_video = {"streams": [{"index": 0, "codec_type": "audio", "time_base": "1/48000"}], "format": {}}
            with self.assertRaisesRegex(MediaAdapterError, "VIDEO_STREAM_MISSING"):
                parse_ffprobe_json(json.dumps(no_video), source_path=source)


class MediaCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dubflow media [space]-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source [input].mp4"
        self.source.write_bytes(b"source")
        self.audio = self.root / "voice track.wav"
        self.audio.write_bytes(b"audio")
        self.subtitle = self.root / "captions [vi].ass"
        self.subtitle.write_text("[Script Info]\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_silent_pcm_uses_integer_source_duration_and_bounded_actual_writes(self):
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent)
        duration = MediaTimeline(Rational(1001, 30000), -12, 241, 229)
        output = self.root / "silent source.wav"
        writes = []
        original = wave.Wave_write.writeframesraw

        def record(writer, data):
            writes.append(len(data))
            return original(writer, data)

        with patch.object(wave.Wave_write, "writeframesraw", record):
            adapter.create_silent_audio(self.source, output, video_duration=duration)
        expected = (241 * 1001 * 48000 + 30000 - 1) // 30000
        with wave.open(str(output), "rb") as reader:
            self.assertEqual((reader.getnframes(), reader.getframerate(), reader.getnchannels(), reader.getsampwidth()),
                             (expected, 48000, 2, 2))
            while pcm := reader.readframes(16384):
                self.assertEqual(pcm, bytes(len(pcm)))
        self.assertGreater(len(writes), 1)
        self.assertLessEqual(max(writes), 65536)
        self.assertEqual(sum(writes), expected * 4)
        self.assertEqual(output.stat().st_size, 44 + expected * 4)
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_silent_pcm_interruption_preserves_prior_output_and_removes_partial(self):
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent)
        output = self.root / "silent.wav"
        duration = MediaTimeline(Rational(1, 1000), 0, 1000, 1000)
        adapter.create_silent_audio(self.source, output, video_duration=duration)
        previous = (output.read_bytes(), output.stat().st_mtime_ns)
        original = wave.Wave_write.writeframesraw
        calls = 0

        def interrupt(writer, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt("interrupted during the second bounded PCM block")
            return original(writer, data)

        with patch.object(wave.Wave_write, "writeframesraw", interrupt), self.assertRaises(KeyboardInterrupt):
            adapter.create_silent_audio(self.source, output, video_duration=duration, overwrite=True)
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), previous)
        with patch("engine.dubflow.media.adapter.os.fsync", side_effect=OSError("disk write failed")):
            with self.assertRaisesRegex(MediaAdapterError, "AUDIO_WRITE_FAILED"):
                adapter.create_silent_audio(self.source, output, video_duration=duration, overwrite=True)
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), previous)
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_silent_pcm_refuses_unknown_duration_invalid_config_and_riff_overflow(self):
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent)
        output = self.root / "refused.wav"
        duration = MediaTimeline(Rational(1, 1), 0, 21600, 21600)
        cases = (({"video_duration": None}, "MEDIA_DURATION_INVALID"),
                 ({"video_duration": MediaTimeline(Rational(1, 1000), 0, 0, 0)}, "MEDIA_DURATION_INVALID"),
                 ({"video_duration": duration, "sample_rate": True}, "AUDIO_CONFIG_INVALID"),
                 ({"video_duration": duration, "channels": 0}, "AUDIO_CONFIG_INVALID"),
                 ({"video_duration": duration, "sample_rate": 384000}, "AUDIO_SIZE_UNSUPPORTED"))
        with patch("engine.dubflow.media.adapter.tempfile.mkstemp", side_effect=AssertionError("must refuse before writing")):
            for options, code in cases:
                with self.subTest(code=code), self.assertRaisesRegex(MediaAdapterError, code):
                    adapter.create_silent_audio(self.source, output, **options)
        self.assertFalse(output.exists())

    def test_probe_and_operations_use_absolute_argv_without_shell(self) -> None:
        probe_runner = RecordingRunner()
        trusted_root = Path(sys.executable).resolve().parent
        probe = MediaProbe(sys.executable, trusted_root=trusted_root, runner=probe_runner)
        result = probe.probe(self.source)
        self.assertEqual(result.video.width, 640)
        self.assertEqual(probe_runner.calls[0][0], sys.executable)
        self.assertIn(str(self.source.resolve()), probe_runner.calls[0])
        self.assertNotIn(" ".join(("-i", str(self.source))), probe_runner.calls[0])

        ffmpeg_runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=trusted_root, runner=ffmpeg_runner)
        extracted = adapter.extract_audio(self.source, self.root / "audio out.wav")
        muxed = adapter.mux_audio(self.source, extracted, self.root / "muxed output.mp4")
        rendered = adapter.render(
            self.source,
            self.root / "rendered [output].mp4",
            subtitle_path=self.subtitle,
            audio_path=extracted,
            preserve_original_audio=False,
        )

        self.assertTrue(extracted.is_file())
        self.assertTrue(muxed.is_file())
        self.assertTrue(rendered.is_file())
        self.assertTrue(all(call[0] == sys.executable for call in ffmpeg_runner.calls))
        render_call = ffmpeg_runner.calls[-1]
        self.assertIn(str(self.subtitle.resolve()), render_call)
        self.assertIn(str(extracted.resolve()), render_call)
        self.assertIn("-c:v", render_call)
        self.assertIn("h264_mf", render_call)
        self.assertTrue(render_call[render_call.index("-vf") + 1].endswith("scale=trunc(iw/2)*2:trunc(ih/2)*2"))
        self.assertIn("-c:a", render_call)
        self.assertNotIn("|", " ".join(render_call))
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_video_bitrate_follows_pixels_per_second_within_bounds(self):
        stream = lambda width, height, rate: MediaStream(0, "video", "h264", None, None, None, None, width=width, height=height,
                                                         frame_rate=Rational.parse(rate, "rate") if rate else None)
        self.assertEqual(video_bitrate(stream(1920, 1080, "30/1")), 7_464_960)
        self.assertEqual(video_bitrate(stream(1024, 576, "30/1")), 2_123_366)
        self.assertEqual(video_bitrate(stream(852, 480, "30000/1001")), 1_500_000)
        self.assertEqual(video_bitrate(stream(3840, 2160, "60/1")), 16_000_000)
        self.assertEqual(video_bitrate(stream(1280, 720, None)), 3_317_760)
        self.assertEqual(video_bitrate(None), 8_000_000)

    def test_render_retries_once_with_openh264_when_media_foundation_fails(self):
        trusted_root = Path(sys.executable).resolve().parent

        class MediaFoundationMissing(RecordingRunner):
            def __call__(self, argv, timeout):
                if "h264_mf" in argv:
                    self.calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, "", "Unknown encoder 'h264_mf'")
                return super().__call__(argv, timeout)

        runner = MediaFoundationMissing()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=trusted_root, runner=runner)
        rendered = adapter.render(self.source, self.root / "fallback.mp4", subtitle_path=self.subtitle)
        self.assertTrue(rendered.is_file())
        first, second = runner.calls[-2], runner.calls[-1]
        self.assertIn("h264_mf", first)
        self.assertNotIn("h264_mf", second)
        encoder = second.index("-c:v")
        self.assertEqual(second[encoder + 1], "libopenh264")
        self.assertIn("yuv420p", second)
        self.assertEqual(first[:encoder], second[:encoder])
        # Without a probe both encoders target 8 Mbit/s; neither falls back to a few hundred kbit/s.
        self.assertEqual(first[first.index("-b:v") + 1], "8000000")
        self.assertEqual(second[second.index("-b:v") + 1], "8000000")
        self.assertEqual(list(self.root.glob(".*.partial")), [])

        failing = RecordingRunner(fail=True)
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=trusted_root, runner=failing)
        with self.assertRaisesRegex(MediaAdapterError, "MEDIA_COMMAND_FAILED"):
            adapter.render(self.source, self.root / "broken.mp4", subtitle_path=self.subtitle)
        self.assertEqual(len(failing.calls), 2, "exactly one fallback attempt")
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_sparse_subtitle_cannot_choose_render_end_and_integer_video_duration_bounds_output(self):
        for burn_in in (False, True):
            with self.subTest(burn_in=burn_in):
                runner = RecordingRunner()
                adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
                adapter.render(self.source, self.root / "bounded.mp4", subtitle_path=self.subtitle,
                    audio_path=self.audio, preserve_original_audio=False, burn_in_subtitles=burn_in,
                    video_duration=MediaTimeline(Rational(1001, 30000), 0, 241, 241), overwrite=True)
                call = runner.calls[-1]
                self.assertNotIn("-shortest", call)
                self.assertEqual(call[call.index("-t") + 1], "8.041367")
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        adapter.render(self.source, self.root / "unknown-duration.mp4", subtitle_path=self.subtitle,
                       audio_path=self.audio, preserve_original_audio=False)
        self.assertNotIn("-shortest", runner.calls[-1])
        self.assertNotIn("-t", runner.calls[-1])
        for invalid in (0, 8.0, MediaTimeline(Rational(1, 1000), 0, 0, 0)):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(MediaAdapterError, "MEDIA_DURATION_INVALID"):
                adapter.render(self.source, self.root / "invalid.mp4", video_duration=invalid)

    def test_source_origin_rescaling_signed_fractional_and_unknown(self):
        video = parse_ffprobe_json(json.dumps(_probe_document()), source_path=self.source).video
        for pts, expected in ((1024, 4000), (-1, -4), (1, 3)):
            origin = replace(video, start_pts=pts, time_base=Rational(1, 12288))
            self.assertEqual(audio_origin_samples(origin, 48000), expected)
        for invalid in (replace(video, start_pts=None), replace(video, time_base=None),
                        replace(video, codec_type="audio"), None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(MediaAdapterError, "MEDIA_TIMELINE_INVALID"):
                audio_origin_samples(invalid, 48000)

    def test_analysis_and_render_share_first_video_origin_before_subtitle_filter(self):
        document = _probe_document()
        document["streams"][0].update(time_base="1/12288", start_pts=1024)
        document["streams"][1]["start_pts"] = 72944
        probe = parse_ffprobe_json(json.dumps(document), source_path=self.source)
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        adapter.extract_audio(self.source, self.root / "aligned.wav", video_stream=probe.video, sample_rate=48000, channels=2)
        call = runner.calls[-1]
        self.assertIn("-copyts", call)
        self.assertEqual(call[call.index("-af") + 1], "aresample=48000:async=1:first_pts=4000")
        for dubbed in (False, True):
            adapter.render(self.source, self.root / "aligned.mp4", subtitle_path=self.subtitle,
                source_probe=probe, audio_path=self.audio if dubbed else None,
                preserve_original_audio=not dubbed, burn_in_subtitles=True, overwrite=True)
            call = runner.calls[-1]
            self.assertIn("-copyts", call)
            self.assertTrue(call[call.index("-vf") + 1].startswith("setpts=PTS-STARTPTS,subtitles="))
            if not dubbed:
                self.assertEqual(call[call.index("-af") + 1], "aresample=48000:async=1:first_pts=4000,asetpts=PTS-STARTPTS")


    def test_burn_in_escapes_filter_path_without_shelling(self) -> None:
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = adapter.render(
            self.source,
            self.root / "burned.mp4",
            subtitle_path=self.subtitle,
            preserve_original_audio=False,
            burn_in_subtitles=True,
        )
        self.assertTrue(output.is_file())
        call = runner.calls[-1]
        filter_value, even = call[call.index("-vf") + 1].rsplit(",", 1)
        self.assertEqual(even, "scale=trunc(iw/2)*2:trunc(ih/2)*2")
        self.assertIn("subtitles=", filter_value)
        self.assertTrue(filter_value.split("=", 1)[1].startswith(".dubflow-subtitle-"))
        self.assertTrue(filter_value.endswith(".ass"))
        self.assertNotIn(str(self.subtitle.resolve()), call)

    def test_failed_command_removes_partial_and_redacts_diagnostic(self) -> None:
        runner = RecordingRunner(fail=True)
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = self.root / "failed.mp4"
        with self.assertRaises(MediaAdapterError) as context:
            adapter.render(self.source, output, preserve_original_audio=False)
        self.assertEqual(context.exception.code, "MEDIA_COMMAND_FAILED")
        self.assertNotIn("should-not-leak", context.exception.condition)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".*.partial")), [])

    def test_rejects_path_lookup_and_existing_output_without_overwrite(self) -> None:
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_NOT_APP_OWNED"):
            MediaProbe("ffprobe")
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_ROOT_REQUIRED"):
            MediaProbe(sys.executable)
        with self.assertRaisesRegex(MediaAdapterError, "EXECUTABLE_OUTSIDE_ROOT"):
            MediaProbe(sys.executable, trusted_root=self.root)
        runner = RecordingRunner()
        adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
        output = self.root / "existing.mp4"
        output.write_bytes(b"keep")
        with self.assertRaisesRegex(MediaAdapterError, "OUTPUT_EXISTS"):
            adapter.render(self.source, output, preserve_original_audio=False)
        self.assertEqual(output.read_bytes(), b"keep")


class BlurRegionTests(unittest.TestCase):
    def test_region_must_fit_the_frame_with_a_coherent_time_range(self):
        self.assertEqual(BlurRegion.from_mapping({"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1}).to_dict(), {"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1})
        timed = BlurRegion.from_mapping({"x": 0, "y": 0, "w": 1, "h": 1, "start_ms": 0, "end_ms": 1500})
        self.assertEqual(timed.to_dict()["end_ms"], 1500)
        invalid = (
            {"x": -0.1, "y": 0.8, "w": 0.5, "h": 0.1}, {"x": 0.6, "y": 0.8, "w": 0.5, "h": 0.1},
            {"x": 0.1, "y": 0.95, "w": 0.5, "h": 0.1}, {"x": 0.1, "y": 0.8, "w": 0.001, "h": 0.1},
            {"x": float("nan"), "y": 0.8, "w": 0.5, "h": 0.1}, {"x": "0.1", "y": 0.8, "w": 0.5, "h": 0.1},
            {"x": 0.1, "y": 0.8, "w": 0.5}, {"x": 0.1, "y": 0.8, "w": 0.5, "h": 0.1, "color": "black"},
            {"x": 0.1, "y": 0.8, "w": 0.5, "h": 0.1, "start_ms": 100}, {"x": 0.1, "y": 0.8, "w": 0.5, "h": 0.1, "start_ms": 900, "end_ms": 900},
            {"x": 0.1, "y": 0.8, "w": 0.5, "h": 0.1, "start_ms": 1.5, "end_ms": 9}, [0.1, 0.8, 0.5, 0.1],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(MediaAdapterError, "MEDIA_BLUR_INVALID"):
                BlurRegion.from_mapping(value)

    def test_filtergraph_chains_regions_and_gates_timed_ones(self):
        graph = blur_filtergraph([BlurRegion(0.08, 0.895, 0.84, 0.06), BlurRegion(0.1, 0.1, 0.2, 0.1, 2000, 4500)])
        self.assertTrue(graph.startswith("split=3[blur_b0][blur_r0][blur_r1];"))
        self.assertIn("crop=w=trunc(iw*0.840000/2)*2:h=trunc(ih*0.060000/2)*2:x=trunc(iw*0.080000/2)*2:y=trunc(ih*0.895000/2)*2,gblur=", graph)
        self.assertNotIn("boxblur", graph, "the shipped LGPL FFmpeg has no boxblur")
        self.assertIn("[blur_b0][blur_k0]overlay=x=trunc(W*0.080000/2)*2:y=trunc(H*0.895000/2)*2[blur_b1]", graph)
        # The last overlay stays unlabeled so later -vf filters chain onto it.
        self.assertTrue(graph.endswith(":enable=between(t\\,2.000\\,4.500)"))
        # Many regions share one band blur and one mask instead of a blur and overlay each.
        many = blur_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1, 1000 * index, 1000 * index + 500) for index in range(13)])
        self.assertEqual(many.count("gblur="), 1)
        self.assertEqual(many.count("drawbox=") - 1, 13)
        self.assertIn("[blur_band]crop=w=iw:h=min(ih\\,trunc(ih*0.900000/2)*2+2)-trunc(ih*0.800000/2)*2:x=0:y=trunc(ih*0.800000/2)*2,gblur=", many)
        self.assertIn("color=white:t=fill:enable=between(t\\,12.000\\,12.500)[blur_mask]", many)
        self.assertTrue(many.endswith("[blur_full][blur_mask]alphamerge[blur_top];[blur_b0][blur_top]overlay"))
        for regions in ([], [BlurRegion(0, 0, 0.5, 0.5)] * (MAX_BLUR_REGIONS + 1)):
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_BLUR_INVALID"):
                blur_filtergraph(regions)

    def test_render_blurs_after_origin_reset_and_before_vietnamese_subtitles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subtitle = root / "captions.ass"
            source.write_bytes(b"video")
            subtitle.write_text("[Script Info]\n", encoding="utf-8")
            probe = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source)
            runner = RecordingRunner()
            adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
            region = BlurRegion(0.1, 0.85, 0.8, 0.1)
            adapter.render(source, root / "out.mp4", subtitle_path=subtitle, source_probe=probe, burn_in_subtitles=True, blur_regions=(region,), overwrite=True)
            filters = runner.calls[-1][runner.calls[-1].index("-vf") + 1]
            self.assertTrue(filters.startswith("setpts=PTS-STARTPTS,split=2"))
            self.assertRegex(filters, r"overlay=x=trunc\(W\*0\.100000/2\)\*2:y=trunc\(H\*0\.850000/2\)\*2,subtitles=\.dubflow-subtitle-")
            # Blur also applies when subtitles are kept as separate files.
            adapter.render(source, root / "soft.mp4", subtitle_path=subtitle, blur_regions=(region,), overwrite=True)
            self.assertTrue(runner.calls[-1][runner.calls[-1].index("-vf") + 1].startswith("split=2"))
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_BLUR_INVALID"):
                adapter.render(source, root / "bad.mp4", blur_regions=({"x": 0},), overwrite=True)

    def test_a_graph_too_long_for_the_command_line_is_read_from_a_file(self):
        # A 4.6-hour video produced 256 timed regions; inline, argv passed 32,767 characters (WinError 206).
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            seen: dict[str, str] = {}

            def runner(argv, timeout):
                script = Path(argv[argv.index("-/vf") + 1])
                seen["graph"] = script.read_text(encoding="utf-8")
                Path(argv[-1]).write_bytes(b"mp4")
                return subprocess.CompletedProcess(argv, 0, "", "")

            adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
            regions = tuple(BlurRegion(0.1, 0.85, 0.8, 0.1, 60_000 * index, 60_000 * index + 4_321) for index in range(MAX_BLUR_REGIONS))
            adapter.render(source, root / "out.mp4", blur_regions=regions, overwrite=True)
            self.assertEqual(seen["graph"], blur_filtergraph(regions) + ",scale=trunc(iw/2)*2:trunc(ih/2)*2")
            self.assertGreater(len(seen["graph"]), 32_767)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["out.mp4", "source.mp4"], "the filter file is removed")


STRIP = "max(2\\,trunc(ih*0.003704/2)*2)"
WIDE = "w=max(2\\,trunc(iw*0.800000/2)*2)"


class CoverStyleTests(unittest.TestCase):
    def test_style_is_validated_and_normalized(self):
        self.assertEqual(CoverStyle.from_mapping({"mode": "stretch"}).to_dict(),
                         {"mode": "stretch", "direction": "top", "strip_px": 4, "color": "#000000", "block": 24, "feather": 0})
        self.assertEqual(CoverStyle.from_mapping({"mode": "color", "color": "#a0b1c2"}).to_dict()["color"], "#A0B1C2")
        invalid = ({}, {"mode": "smear"}, {"mode": "stretch", "direction": "left"}, {"mode": "stretch", "strip_px": 1},
                   {"mode": "stretch", "strip_px": 13}, {"mode": "stretch", "strip_px": 4.0}, {"mode": "mosaic", "block": 7},
                   {"mode": "mosaic", "block": 65}, {"mode": "color", "color": "red"}, {"mode": "stretch", "feather": 9},
                   {"mode": "stretch", "feather": True}, {"mode": "blur", "sigma": 40}, ["stretch"])
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(MediaAdapterError, "MEDIA_COVER_INVALID"):
                CoverStyle.from_mapping(value)

    def test_without_a_style_or_with_blur_the_graph_is_todays_blur(self):
        for regions in ([BlurRegion(0.08, 0.895, 0.84, 0.06), BlurRegion(0.1, 0.1, 0.2, 0.1, 2000, 4500)],
                        [BlurRegion(0.1, 0.8, 0.8, 0.1, 1000 * index, 1000 * index + 500) for index in range(13)]):
            self.assertEqual(cover_filtergraph(regions), blur_filtergraph(regions))
            self.assertEqual(cover_filtergraph(regions, CoverStyle("blur", feather=4)), blur_filtergraph(regions))

    def test_stretch_pulls_the_strip_above_over_the_region_while_it_is_on(self):
        graph = cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1, 1000, 2500)], CoverStyle("stretch"))
        self.assertEqual(graph, ";".join([
            "split=3[cv0m][cv0s][cv0r]",
            f"[cv0s]crop={WIDE}:h={STRIP}:x=trunc(iw*0.100000/2)*2:y=max(0\\,trunc(ih*0.800000/2)*2-oh)[cv0c]",
            "[cv0c][cv0r]scale=w=max(2\\,trunc(rw*0.800000/2)*2):h=max(2\\,trunc(rh*0.100000/2)*2):flags=bilinear[cv0p]",
            "[cv0m][cv0p]overlay=x=trunc(W*0.100000/2)*2:y=trunc(H*0.800000/2)*2:enable=between(t\\,1.000\\,2.500)",
        ]))
        # A strip of 12 px on the 1080-line scale; any resolution gets the same share of the frame.
        self.assertIn("h=max(2\\,trunc(ih*0.011111/2)*2)", cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("stretch", strip_px=12)))

    def test_strips_switch_sides_at_the_frame_edge(self):
        below = "y=min(ih-oh\\,trunc(ih*0.100000/2)*2)"
        # No room above a region at the very top: the strip comes from below it.
        self.assertIn(f"[cv0s]crop={WIDE}:h={STRIP}:x=trunc(iw*0.100000/2)*2:{below}[cv0c]",
                      cover_filtergraph([BlurRegion(0.1, 0.0, 0.8, 0.1)], CoverStyle("stretch")))
        # No room below a region touching the bottom: the strip comes from above it.
        self.assertIn(f"[cv0s]crop={WIDE}:h={STRIP}:x=trunc(iw*0.100000/2)*2:y=max(0\\,trunc(ih*0.900000/2)*2-oh)[cv0c]",
                      cover_filtergraph([BlurRegion(0.1, 0.9, 0.8, 0.1)], CoverStyle("stretch", "bottom")))
        self.assertIn("y=min(ih-oh\\,trunc(ih*0.600000/2)*2)[cv0c]", cover_filtergraph([BlurRegion(0.1, 0.5, 0.8, 0.1)], CoverStyle("stretch", "bottom")))

    def test_both_stacks_the_upper_half_from_above_on_the_lower_half_from_below(self):
        graph = cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("stretch", "both"))
        self.assertEqual(graph, ";".join([
            "split=5[cv0m][cv0s][cv0t][cv0r][cv0q]",
            f"[cv0s]crop={WIDE}:h={STRIP}:x=trunc(iw*0.100000/2)*2:y=max(0\\,trunc(ih*0.800000/2)*2-oh)[cv0c]",
            f"[cv0t]crop={WIDE}:h={STRIP}:x=trunc(iw*0.100000/2)*2:y=min(ih-oh\\,trunc(ih*0.900000/2)*2)[cv0e]",
            "[cv0c][cv0r]scale=w=max(2\\,trunc(rw*0.800000/2)*2):h=max(2\\,trunc(rh*0.050000/2)*2):flags=bilinear[cv0u]",
            "[cv0e][cv0q]scale=w=max(2\\,trunc(rw*0.800000/2)*2):h=max(2\\,trunc(rh*0.100000/2)*2-trunc(rh*0.050000/2)*2):flags=bilinear[cv0l]",
            "[cv0u][cv0l]vstack[cv0p]",
            "[cv0m][cv0p]overlay=x=trunc(W*0.100000/2)*2:y=trunc(H*0.800000/2)*2",
        ]))

    def test_color_draws_boxes_and_mosaic_scales_down_and_up(self):
        color = cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1, 0, 500), BlurRegion(0.2, 0.1, 0.3, 0.1, 100, 200)], CoverStyle("color", color="#ff8800"))
        self.assertEqual(color, ",".join([
            "drawbox=x=trunc(iw*0.100000/2)*2:y=trunc(ih*0.800000/2)*2:w=max(2\\,trunc(iw*0.800000/2)*2):h=max(2\\,trunc(ih*0.100000/2)*2)"
            ":color=0xFF8800:t=fill:enable=between(t\\,0.000\\,0.500)",
            "drawbox=x=trunc(iw*0.200000/2)*2:y=trunc(ih*0.100000/2)*2:w=max(2\\,trunc(iw*0.300000/2)*2):h=max(2\\,trunc(ih*0.100000/2)*2)"
            ":color=0xFF8800:t=fill:enable=between(t\\,0.100\\,0.200)",
        ]))
        mosaic = cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("mosaic", block=16))
        self.assertEqual(mosaic, ";".join([
            "split=3[cv0m][cv0s][cv0r]",
            # 108 px tall at 1080p in 16 px blocks: 7 rows at any resolution.
            f"[cv0s]crop={WIDE}:h=max(2\\,trunc(ih*0.100000/2)*2):x=trunc(iw*0.100000/2)*2:y=trunc(ih*0.800000/2)*2,"
            "scale=w=max(1\\,round(iw*7/ih)):h=7:flags=area[cv0d]",
            "[cv0d][cv0r]scale=w=max(2\\,trunc(rw*0.800000/2)*2):h=max(2\\,trunc(rh*0.100000/2)*2):flags=neighbor[cv0p]",
            "[cv0m][cv0p]overlay=x=trunc(W*0.100000/2)*2:y=trunc(H*0.800000/2)*2",
        ]))

    def test_feather_fades_the_cover_edges_through_an_alpha_mask(self):
        graph = cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("stretch", feather=4))
        self.assertIn(";[cv0p]format=yuva420p,split=2[cv0pa][cv0pm];[cv0pm]format=gray,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,"
                      "drawbox=x=trunc(ih*0.037037):y=trunc(ih*0.037037):w=max(2\\,iw-2*trunc(ih*0.037037)):h=max(2\\,ih-2*trunc(ih*0.037037)):"
                      "color=white:t=fill,lut=c0=clip((val-16)*255/219\\,0\\,255),gblur=sigma=2[cv0pk];[cv0pa][cv0pk]alphamerge[cv0pf];"
                      "[cv0m][cv0pf]overlay=", graph)
        self.assertNotIn("alphamerge", cover_filtergraph([BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("color", feather=4)))

    def test_regions_sharing_a_rectangle_share_one_chain_with_chunked_windows(self):
        regions = [BlurRegion(0.1, 0.8, 0.8, 0.1, 1000 * index, 1000 * index + 500) for index in range(100)]
        regions.append(BlurRegion(0.1, 0.8, 0.8, 0.1, 250, 900))  # Overlaps the first window: coalesced.
        graph = cover_filtergraph(regions, CoverStyle("stretch"))
        self.assertEqual(graph.count("crop="), 1)
        self.assertEqual(graph.count("between("), 100)
        self.assertIn("between(t\\,0.000\\,0.900)+between(t\\,1.000\\,1.500)", graph)
        # 100 windows in chunks of 48: one picture split to three overlays chained on the main picture.
        self.assertIn("[cv0p]split=3[cv0p0][cv0p1][cv0p2];[cv0m][cv0p0]overlay=", graph)
        self.assertIn("[cv0m1][cv0p1]overlay=", graph)
        self.assertRegex(graph, r";\[cv0m2\]\[cv0p2\]overlay=[^;\[]*between\(t\\,99\.000\\,99\.500\)$")
        color = cover_filtergraph(regions, CoverStyle("color"))
        self.assertEqual(color.count("drawbox="), 3)
        # A region covered for the whole video makes its rectangle always on.
        always = cover_filtergraph([*regions, BlurRegion(0.1, 0.8, 0.8, 0.1)], CoverStyle("mosaic"))
        self.assertNotIn("enable=", always)

    def test_many_rectangles_merge_per_caption_line_and_chain_top_down(self):
        # Auto-detected captions: one line at slightly different widths, and a second line above it.
        lower = [BlurRegion(round(0.2 + 0.001 * (index % 20), 4), 0.85, round(0.6 - 0.002 * (index % 20), 4), 0.08, 100 * index, 100 * index + 80)
                 for index in range(200)]
        upper = [BlurRegion(0.25, 0.76, 0.5, 0.08, 100 * index + 20, 100 * index + 60) for index in range(56)]
        graph = cover_filtergraph(lower + upper, CoverStyle("stretch"))
        self.assertEqual(graph.count("crop="), 2, "one chain per caption line")
        self.assertEqual(graph.count("between("), 256)
        # The upper line is covered first, so the lower line's strip above reads the covered picture.
        self.assertLess(graph.index("y=max(0\\,trunc(ih*0.760000/2)*2-oh)"), graph.index("y=max(0\\,trunc(ih*0.850000/2)*2-oh)"))
        self.assertIn(f"crop=w=max(2\\,trunc(iw*0.600000/2)*2):h={STRIP}:x=trunc(iw*0.200000/2)*2:", graph, "the union of the line's widths")
        bottom_up = cover_filtergraph(lower + upper, CoverStyle("stretch", "bottom"))
        self.assertLess(bottom_up.index("trunc(ih*0.930000/2)*2"), bottom_up.index("trunc(ih*0.840000/2)*2"))
        # Scattered rectangles never exceed the chain budget.
        scattered = [BlurRegion(0.05 * (index % 10), 0.06 * index, 0.1, 0.05, 10 * index, 10 * index + 5) for index in range(16)]
        self.assertEqual(cover_filtergraph(scattered, CoverStyle("mosaic")).count("crop="), 12)
        for regions in ([], [BlurRegion(0, 0, 0.5, 0.5)] * (MAX_BLUR_REGIONS + 1)):
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_BLUR_INVALID"):
                cover_filtergraph(regions, CoverStyle("stretch"))

    def test_render_covers_in_the_chosen_style_before_the_subtitles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subtitle = root / "captions.ass"
            source.write_bytes(b"video")
            subtitle.write_text("[Script Info]\n", encoding="utf-8")
            probe = parse_ffprobe_json(json.dumps(_probe_document()), source_path=source)
            runner = RecordingRunner()
            adapter = FfmpegMediaAdapter(sys.executable, trusted_root=Path(sys.executable).resolve().parent, runner=runner)
            region = BlurRegion(0.1, 0.85, 0.8, 0.1)
            adapter.render(source, root / "out.mp4", subtitle_path=subtitle, source_probe=probe, burn_in_subtitles=True,
                           blur_regions=(region,), cover_style=CoverStyle("stretch"), overwrite=True)
            filters = runner.calls[-1][runner.calls[-1].index("-vf") + 1]
            self.assertTrue(filters.startswith("setpts=PTS-STARTPTS,split=3[cv0m][cv0s][cv0r];"))
            self.assertRegex(filters, r"overlay=x=trunc\(W\*0\.100000/2\)\*2:y=trunc\(H\*0\.850000/2\)\*2,subtitles=\.dubflow-subtitle-")
            # A style alone draws nothing.
            adapter.render(source, root / "plain.mp4", cover_style=CoverStyle("stretch"), overwrite=True)
            self.assertEqual(runner.calls[-1][runner.calls[-1].index("-vf") + 1], "scale=trunc(iw/2)*2:trunc(ih/2)*2")
            with self.assertRaisesRegex(MediaAdapterError, "MEDIA_COVER_INVALID"):
                adapter.render(source, root / "bad.mp4", blur_regions=(region,), cover_style={"mode": "stretch"}, overwrite=True)


@unittest.skipUnless(os.environ.get("DUBFLOW_TEST_FFMPEG"), "set DUBFLOW_TEST_FFMPEG to a real ffmpeg.exe to run")
class RealFfmpegBlurTests(unittest.TestCase):
    def gray(self, ffmpeg: str, video: Path, seconds: float) -> bytes:
        return subprocess.run([ffmpeg, "-v", "error", "-ss", str(seconds), "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                              check=True, capture_output=True).stdout

    def test_caption_band_is_smoothed_only_inside_the_region_and_time(self):
        ffmpeg = os.environ["DUBFLOW_TEST_FFMPEG"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "caption.mp4"
            # A hard white "caption" bar with sharp edges near the bottom, plus a sharp bar near the top.
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=3:r=10",
                            "-vf", "drawbox=x=60:y=200:w=200:h=16:color=white:t=fill,drawbox=x=60:y=20:w=200:h=16:color=white:t=fill",
                            "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
            adapter = FfmpegMediaAdapter(ffmpeg, trusted_root=Path(ffmpeg).resolve().parent)
            output = adapter.render(source, root / "blurred.mp4", blur_regions=(BlurRegion(0.1, 0.75, 0.8, 0.2, 1000, 3000),), overwrite=True)
            before, after = self.gray(ffmpeg, output, 0.5), self.gray(ffmpeg, output, 2.0)
            pixel = lambda frame, x, y: frame[y * 320 + x]
            # Before 1 s the caption is untouched: its centre is white and the gap below it is black.
            self.assertGreater(pixel(before, 160, 208), 200)
            self.assertLess(pixel(before, 160, 222), 40)
            # Inside the window the bar's centre darkens and its surroundings brighten.
            self.assertLess(pixel(after, 160, 208), 200)
            self.assertGreater(pixel(after, 160, 222), 40)
            # The top bar lies outside the region and stays sharp.
            self.assertGreater(pixel(after, 160, 28), 200)
            self.assertLess(pixel(after, 160, 42), 40)

    def test_the_most_regions_blur_through_one_mask_read_from_a_file(self):
        ffmpeg = os.environ["DUBFLOW_TEST_FFMPEG"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "caption.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=3:r=10",
                            "-vf", "drawbox=x=60:y=200:w=200:h=16:color=white:t=fill,drawbox=x=60:y=20:w=200:h=16:color=white:t=fill",
                            "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
            adapter = FfmpegMediaAdapter(ffmpeg, trusted_root=Path(ffmpeg).resolve().parent)
            # One region covers the bottom bar from 1 s; the others lie after the video ends.
            later = tuple(BlurRegion(0.1, 0.75, 0.8, 0.2, 10_000 + 10 * index, 10_005 + 10 * index) for index in range(MAX_BLUR_REGIONS - 1))
            output = adapter.render(source, root / "blurred.mp4", blur_regions=(BlurRegion(0.1, 0.75, 0.8, 0.2, 1000, 3000), *later), overwrite=True)
            before, after = self.gray(ffmpeg, output, 0.5), self.gray(ffmpeg, output, 2.0)
            pixel = lambda frame, x, y: frame[y * 320 + x]
            self.assertGreater(pixel(before, 160, 208), 200)
            self.assertLess(pixel(before, 160, 222), 40)
            self.assertLess(pixel(after, 160, 208), 200)
            self.assertGreater(pixel(after, 160, 222), 40)
            self.assertGreater(pixel(after, 160, 28), 200)
            self.assertLess(pixel(after, 160, 42), 40)
            # Outside the region, beside the bar, the frame is untouched.
            self.assertLess(pixel(after, 10, 208), 40)

    def test_cover_styles_hide_the_caption_only_inside_the_region_and_time(self):
        ffmpeg = os.environ["DUBFLOW_TEST_FFMPEG"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "caption.mp4"
            # Black scene with a white "caption" bar near the bottom and a sharp bar near the top.
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=3:r=10",
                            "-vf", "drawbox=x=60:y=200:w=200:h=16:color=white:t=fill,drawbox=x=60:y=20:w=200:h=16:color=white:t=fill",
                            "-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p", str(source)], check=True)
            adapter = FfmpegMediaAdapter(ffmpeg, trusted_root=Path(ffmpeg).resolve().parent)
            pixel = lambda frame, x, y: frame[y * 320 + x]
            region = BlurRegion(0.1, 0.75, 0.8, 0.2, 1000, 3000)
            for style, inside in ((CoverStyle("stretch"), lambda value: value < 40), (CoverStyle("stretch", "bottom"), lambda value: value < 40),
                                  (CoverStyle("stretch", "both", feather=2), lambda value: value < 40),
                                  (CoverStyle("color", color="#808080"), lambda value: 100 < value < 150),
                                  (CoverStyle("mosaic", block=64), lambda value: value < 200)):
                with self.subTest(style=style):
                    output = adapter.render(source, root / f"{style.mode}-{style.direction}.mp4", blur_regions=(region,), cover_style=style, overwrite=True)
                    before, after = self.gray(ffmpeg, output, 0.5), self.gray(ffmpeg, output, 2.0)
                    self.assertGreater(pixel(before, 160, 208), 200)
                    self.assertTrue(inside(pixel(after, 160, 208)), pixel(after, 160, 208))
                    # The top bar and the scene beside the region stay as they were.
                    self.assertGreater(pixel(after, 160, 28), 200)
                    self.assertLess(pixel(after, 10, 208), 40)

    def test_odd_dimensions_render_to_even_h264(self):
        ffmpeg = os.environ["DUBFLOW_TEST_FFMPEG"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "odd.mkv"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=853x479:d=1:r=10",
                            "-c:v", "ffv1", str(source)], check=True)
            adapter = FfmpegMediaAdapter(ffmpeg, trusted_root=Path(ffmpeg).resolve().parent)
            output = adapter.render(source, root / "even.mp4", preserve_original_audio=False, overwrite=True)
            probe = subprocess.run([Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"), "-v", "error",
                                    "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(output)],
                                   check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(probe, "h264,852,478")


if __name__ == "__main__":
    unittest.main()

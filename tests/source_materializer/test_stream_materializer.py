"""Deterministic failure/recovery coverage; native media proof is separate."""

from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, HttpResponse, MediaMaterializer
from engine.dubflow.download.source_adapter import MediaCandidate
from engine.dubflow.download.stream_materializer import FfmpegStreamMuxer, StreamInfo, StreamMaterializer, _ownership, _stream_info


VIDEO = MediaCandidate("v", "https://cdn.example.test/v.m4s?token=secret", "dash", "video/mp4", 1280, 720, False)
AUDIO = MediaCandidate("a", "https://cdn.example.test/a.m4s?token=secret", "dash", "audio/mp4")
VINFO = StreamInfo("video", "h264", 0, 180000)
AINFO = StreamInfo("audio", "aac", 0, 180000)


class Transport:
    def __init__(self):
        self.fail_audio = False
        self.calls = []

    def open(self, url, *, headers=None):
        self.calls.append(url)
        if self.fail_audio and "/a.m4s" in url:
            raise DownloadError(DownloadErrorCode.NETWORK, "recorded interruption")
        data = b"audio" if "/a.m4s" in url else b"video"
        return HttpResponse(200, {"content-length": str(len(data))}, BytesIO(data), url)


class Muxer:
    fingerprint = "a" * 64

    def __init__(self):
        self.fail = False
        self.invalid_output = False
        self.short_audio = False
        self.commands = []

    def probe(self, path, *, cancel=None):
        if path.name == "video.stream":
            return (VINFO,)
        if path.name == "audio.stream":
            return (StreamInfo("audio", "aac", 0, 1000) if self.short_audio else AINFO,)
        return (VINFO,) if self.invalid_output else (VINFO, AINFO)

    def mux(self, video, audio, output, *, cancel, max_bytes):
        self.commands.append((video, audio, output))
        output.write_bytes(b"video+audio")
        if self.fail:
            raise DownloadError(DownloadErrorCode.SOURCE_CHANGED, "recorded mux failure")


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "final.mp4"
        self.transport = Transport()
        self.muxer = Muxer()
        self.materializer = StreamMaterializer(self.muxer, materializer=MediaMaterializer(self.transport))

    def download(self, **kwargs):
        return self.materializer.download(VIDEO, self.destination, audio=AUDIO, root=self.root, **kwargs)

    def test_publishes_only_after_both_streams_and_output_probe(self):
        result = self.download()
        self.assertEqual(self.destination.read_bytes(), b"video+audio")
        self.assertEqual(result.sha256, hashlib.sha256(b"video+audio").hexdigest())
        self.assertEqual(len(self.transport.calls), 2)
        for record in self.root.rglob("*.json"):
            text = record.read_text()
            self.assertNotIn("secret", text)
            self.assertNotIn("example.test", text)
            self.assertEqual(json.loads(text)["producer_version"], "source-stream-mux/1")
        self.assertTrue(all(path.is_absolute() for path in self.muxer.commands[0]))

    def test_completed_video_recovered_after_audio_interruption(self):
        self.destination.write_bytes(b"previous")
        self.transport.fail_audio = True
        with self.assertRaises(DownloadError):
            self.download()
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.transport.fail_audio = False
        # A new materializer models a fresh worker process.
        self.materializer = StreamMaterializer(self.muxer, materializer=MediaMaterializer(self.transport))
        result = self.download()
        self.assertTrue(result.resumed)
        self.assertEqual(sum("/v.m4s" in url for url in self.transport.calls), 1)
        self.assertEqual(sum("/a.m4s" in url for url in self.transport.calls), 2)

    def test_corrupt_checkpoint_redownloads_only_corrupt_stream(self):
        self.download()
        next(self.root.rglob("video.stream")).write_bytes(b"bad!!")
        result = self.download()
        self.assertTrue(result.resumed)  # the healthy audio is recovered
        self.assertEqual(sum("/v.m4s" in url for url in self.transport.calls), 2)
        self.assertEqual(sum("/a.m4s" in url for url in self.transport.calls), 1)

    def test_changed_producer_does_not_reuse_stream_checkpoints(self):
        self.download()
        self.muxer.fingerprint = "b" * 64
        result = self.download()
        self.assertFalse(result.resumed)
        self.assertEqual(len(self.transport.calls), 4)

    def test_mux_failure_and_invalid_output_preserve_existing_file(self):
        for flag in ("fail", "invalid_output", "short_audio"):
            with self.subTest(flag=flag):
                self.destination.write_bytes(b"previous")
                setattr(self.muxer, flag, True)
                with self.assertRaises(DownloadError):
                    self.download()
                self.assertEqual(self.destination.read_bytes(), b"previous")
                self.assertEqual(list(self.root.glob(".source-mux-*")), [])
                setattr(self.muxer, flag, False)

    def test_final_size_and_hash_apply_to_muxed_file(self):
        self.destination.write_bytes(b"previous")
        for kwargs, code in (({"expected_size": 5}, DownloadErrorCode.SIZE_LIMIT),
                             ({"expected_sha256": "0" * 64}, DownloadErrorCode.CHECKSUM_MISMATCH)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DownloadError) as context:
                    self.download(**kwargs)
                self.assertEqual(context.exception.code, code)
                self.assertEqual(self.destination.read_bytes(), b"previous")

    def test_cancelled_item_never_publishes(self):
        self.destination.write_bytes(b"previous")
        with self.assertRaises(DownloadError) as context:
            self.download(cancel=lambda: True)
        self.assertEqual(context.exception.code, DownloadErrorCode.CANCELLED)
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.assertEqual(self.transport.calls, [])

    def test_live_owner_blocks_second_writer_and_releases_on_exit(self):
        with _ownership(self.destination):
            with self.assertRaises(DownloadError) as context:
                self.download()
            self.assertEqual(context.exception.action, "wait_for_owner")
            self.assertEqual(self.transport.calls, [])
        self.download()
        self.assertTrue(self.destination.is_file())

    def test_manifest_is_not_saved_as_media_and_missing_audio_is_explicit(self):
        for candidate, audio in ((MediaCandidate("manifest", "https://cdn.example.test/index.mpd", "dash", "video/mp4", has_audio=False), AUDIO), (VIDEO, None)):
            with self.subTest(candidate=candidate.candidate_id):
                with self.assertRaises(DownloadError) as context:
                    self.materializer.download(candidate, self.destination, audio=audio)
                self.assertEqual(context.exception.code, DownloadErrorCode.UNSUPPORTED)
                self.assertFalse(self.destination.exists())
                self.assertEqual(self.transport.calls, [])

    def test_symlinked_scratch_is_rejected_without_writing_outside(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            self.destination.with_name("final.mp4.streams").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("host does not permit symbolic link creation")
        with self.assertRaises(DownloadError) as context:
            self.download()
        self.assertEqual(context.exception.code, DownloadErrorCode.INVALID_DESTINATION)
        self.assertEqual(list(outside.iterdir()), [])

    def test_runtime_pin_rejects_mutation_and_root_escape(self):
        executable = self.root / "ffmpeg.exe"
        executable.write_bytes(b"fixture")
        digest = hashlib.sha256(b"fixture").hexdigest()
        muxer = FfmpegStreamMuxer(executable, executable, trusted_root=self.root, ffmpeg_sha256=digest, ffprobe_sha256=digest)
        executable.write_bytes(b"changed")
        with self.assertRaises(DownloadError) as context:
            muxer._run((str(executable),), None)
        self.assertEqual(context.exception.code, DownloadErrorCode.CHECKSUM_MISMATCH)
        with self.assertRaises(DownloadError):
            FfmpegStreamMuxer(executable, executable, trusted_root=self.root / "other", ffmpeg_sha256=digest, ffprobe_sha256=digest)

    def test_source_timestamps_use_integer_time_base(self):
        info = _stream_info({"codec_type": "video", "codec_name": "h264", "time_base": "1/24000", "duration_ts": 48048, "start_pts": 12000})
        self.assertEqual(info.duration_ticks, 180180)
        self.assertEqual(info.start_ticks, 45000)
        with self.assertRaises(DownloadError):
            _stream_info({"codec_type": "audio", "codec_name": "aac", "time_base": "1/0", "duration_ts": 100, "start_pts": 0})


if __name__ == "__main__":
    unittest.main()

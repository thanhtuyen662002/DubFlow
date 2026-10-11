"""Deterministic failure/recovery coverage; native media proof is separate."""

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, HttpResponse, MediaMaterializer
from engine.dubflow.download.source_adapter import MediaCandidate
from engine.dubflow.download.stream_materializer import FfmpegStreamMuxer, StreamInfo, StreamMaterializer, _ownership, _packet_span, _stream_info


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

    @unittest.skipUnless(os.name == "nt", "Windows Job lifetime evidence")
    def test_windows_parent_loss_retires_owned_media_process_tree(self):
        import ctypes
        from ctypes import wintypes

        repo = Path(__file__).resolve().parents[2]
        pid_file = self.root / "media-processes.json"
        child = "import time;time.sleep(60)"
        media = "import json,os,pathlib,subprocess,sys,time;p=subprocess.Popen([sys.executable,'-I','-S','-B','-c'," + repr(child) + "]);pathlib.Path(" + repr(str(pid_file)) + ").write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
        parent = "import sys;from pathlib import Path;sys.path.insert(0," + repr(str(repo)) + ");from engine.dubflow.download.stream_materializer import FfmpegStreamMuxer,_file_digest;p=Path(sys.executable);m=FfmpegStreamMuxer(p,p,trusted_root=p.parent,ffmpeg_sha256=_file_digest(p),ffprobe_sha256=_file_digest(p));m._run((str(p),'-I','-S','-B','-c'," + repr(media) + "),None)"
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", parent],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        handles = []
        try:
            deadline = time.monotonic() + 15
            while not pid_file.exists():
                self.assertIsNone(process.poll(), "media owner exited before containment proof")
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            # Wait until the writer has closed the small private receipt.
            while True:
                try:
                    pids = json.loads(pid_file.read_text())
                    break
                except (OSError, ValueError):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
            for pid in pids:
                handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                self.assertTrue(handle)
                handles.append(handle)
                self.assertEqual(api.WaitForSingleObject(handle, 0), 258)  # WAIT_TIMEOUT/live
            process.kill(); process.wait(timeout=10)
            for handle in handles:
                self.assertEqual(api.WaitForSingleObject(handle, 10000), 0)  # Signalled/dead
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            for handle in handles:
                api.CloseHandle(handle)

    def test_selected_stream_progress_includes_verified_reused_video_without_counting_mux_bytes(self):
        self.transport.fail_audio = True
        observed = []
        with self.assertRaises(DownloadError):
            self.download(progress=lambda downloaded, total: observed.append((downloaded, total)))
        self.assertFalse(self.destination.exists())
        self.assertEqual(observed[-1], (5, None))
        self.transport.fail_audio = False
        observed.clear()
        result = self.download(progress=lambda downloaded, total: observed.append((downloaded, total)))
        self.assertTrue(result.resumed)
        self.assertEqual(observed, [(5, None), (5, 10), (10, 10)])
        self.assertEqual(result.size_bytes, 11)
        self.assertEqual(self.transport.calls.count(VIDEO.locator), 1)

    def test_progress_observer_rejects_transfer_without_publishing_a_mux(self):
        self.destination.write_bytes(b"previous")

        def progress(downloaded, total):
            if downloaded:
                raise RuntimeError("late item dispatch")

        with self.assertRaisesRegex(RuntimeError, "late item"):
            self.download(progress=progress)
        self.assertEqual(self.destination.read_bytes(), b"previous")
        self.assertEqual(self.muxer.commands, [])

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

    def test_missing_stream_duration_uses_actual_packet_extent_and_rational_base(self):
        info = _stream_info({"codec_type": "video", "codec_name": "h264", "time_base": "1/24000"},
            packet_extent=(12000, 60048))
        self.assertEqual(info, StreamInfo("video", "h264", 45000, 180180))
        observed = _stream_info({"codec_type": "audio", "codec_name": "ac3", "time_base": "1/1000"},
            packet_extent=(0, 888032))
        self.assertEqual(observed.duration_ticks, 79922880)
        for extent in ((0, 0), (10, 5), (False, 5), (0, 10**15)):
            with self.subTest(extent=extent), self.assertRaises(DownloadError):
                _stream_info({"codec_type": "video", "codec_name": "h264", "time_base": "1/1000"}, packet_extent=extent)

    def test_packet_extent_does_not_replace_reported_or_malformed_duration(self):
        base = {"codec_type": "video", "codec_name": "h264", "time_base": "1/24000", "start_pts": 0}
        valid = _stream_info(dict(base, duration_ts=48048), packet_extent=(0, 888032))
        self.assertEqual(valid.duration_ticks, 180180)
        for malformed in ({"duration": "NaN"}, {"duration_ts": "wrong"}, {"time_base": "1/0"}):
            with self.subTest(malformed=malformed), self.assertRaises(DownloadError):
                _stream_info(dict(base, **malformed), packet_extent=(0, 888032))

    def test_compact_packet_requires_bounded_integer_pts_and_duration(self):
        self.assertEqual(_packet_span(b"stream_index=0|pts=-1|duration=41\n", {0, 1}), (0, -1, 40))
        self.assertIsNone(_packet_span(b"stream_index=2|pts=N/A|duration=N/A\n", {0, 1}))
        for line in (b"stream_index=0|pts=N/A|duration=41\n", b"stream_index=0|pts=1.0|duration=41\n",
            b"stream_index=0|pts=0|duration=0\n", b"stream_index=0|pts=0|duration=-1\n",
            b"stream_index=0|pts=0|pts=10|duration=41\n", b"stream_index=0|pts=0\n",
            b"stream_index=0|pts=9223372036854775807|duration=1\n", b"stream_index=32|pts=0|duration=41\n",
            b"stream_index=0|pts=0|duration=" + b"1"*513):
            with self.subTest(line=line[:80]), self.assertRaises(DownloadError) as context:
                _packet_span(line, {0, 1})
            self.assertEqual(context.exception.code, DownloadErrorCode.SOURCE_CHANGED)

    def test_packet_probe_cancellation_keeps_typed_cancelled_error(self):
        executable = self.root / "ffprobe.exe"
        executable.write_bytes(b"fixture")
        digest = hashlib.sha256(b"fixture").hexdigest()
        class CancelledMuxer(FfmpegStreamMuxer):
            def _run(self, *args, **kwargs):
                return json.dumps({"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "time_base": "1/1000"}]}).encode()
            def _packet_extents(self, *args, **kwargs):
                raise DownloadError(DownloadErrorCode.CANCELLED, "recorded cancel during packet scan")
        muxer = CancelledMuxer(executable, executable, trusted_root=self.root,
            ffmpeg_sha256=digest, ffprobe_sha256=digest)
        with self.assertRaises(DownloadError) as context:
            muxer.probe(executable)
        self.assertEqual(context.exception.code, DownloadErrorCode.CANCELLED)


if __name__ == "__main__":
    unittest.main()

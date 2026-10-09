from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from hashlib import sha256
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine.dubflow.worker.production_job import (
    ProductionJobError,
    TextCue,
    WorkerConfig,
    _load_sidecar,
    _validate_rendered_audio,
    _write_subtitles,
)
from engine.dubflow.media import parse_ffprobe_json, MediaAdapterError, Rational
from engine.dubflow.worker import production_job as worker
from engine.dubflow.worker.protocol import Envelope, MessageType


class ProductionWorkerTests(unittest.TestCase):
    def test_render_duration_preserves_source_integer_base_before_canonical_fallback(self):
        probe = SimpleNamespace(video=SimpleNamespace(time_base=Rational(1001, 30000), duration_ticks=241), duration_ticks=8042)
        duration = worker._source_render_duration(probe)
        self.assertEqual(duration.time_base, Rational(1001, 30000))
        self.assertEqual(duration.duration_ticks, 241)
        probe.video = SimpleNamespace(time_base=None, duration_ticks=None)
        fallback = worker._source_render_duration(probe)
        self.assertEqual(fallback.duration_ticks, 8042)
        self.assertEqual(fallback.time_base, Rational(1, 1000))
        probe.duration_ticks = None
        self.assertIsNone(worker._source_render_duration(probe))

    def test_editable_copy_has_bounded_reads_and_reuses_verified_completed_asset(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source.wav", root / "editable.wav"
            digest = sha256()
            block = bytes(range(256)) * 4096
            with source.open("wb") as writer:
                for _ in range(32):
                    writer.write(block)
                    digest.update(block)
            expected = "sha256:" + digest.hexdigest()
            original_open = Path.open

            class BoundedReader:
                def __init__(self, handle):
                    self.handle = handle
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return self.handle.__exit__(*args)
                def fileno(self):
                    return self.handle.fileno()
                def read(self, size=-1):
                    if not 0 < size <= 1024 * 1024:
                        raise AssertionError("editable publication made an unbounded read")
                    return self.handle.read(size)

            def guarded(path, mode="r", *args, **kwargs):
                handle = original_open(path, mode, *args, **kwargs)
                return BoundedReader(handle) if mode == "rb" else handle

            with patch.object(Path, "open", guarded), patch.object(Path, "read_bytes", side_effect=AssertionError("whole payload read")):
                worker._copy_editable_artifact(source, target, expected_hash=expected)
            self.assertEqual(worker._sha256(target), expected)
            modified = target.stat().st_mtime_ns
            with patch.object(worker.os, "replace", side_effect=AssertionError("completed copy was repeated")):
                worker._copy_editable_artifact(source, target, expected_hash=expected)
            self.assertEqual(target.stat().st_mtime_ns, modified)
            with source.open("r+b") as stream:
                stream.write(b"changed")
            with self.assertRaisesRegex(ProductionJobError, "EDITABLE_ARTIFACT_CHANGED"):
                worker._copy_editable_artifact(source, target, expected_hash=expected)
            self.assertEqual(worker._sha256(target), expected)

    def test_editable_corruption_storage_or_promotion_failure_preserves_prior_asset(self):
        for failure in ("checksum", "disk", "rename"):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                root = Path(directory)
                source, target = root / "source.wav", root / "editable.wav"
                source.write_bytes(b"new validated data")
                target.write_bytes(b"previous valid artifact")
                expected = worker._sha256(source)
                if failure == "checksum":
                    expected = "sha256:" + "0" * 64
                    context = patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=10**9))
                    code = "EDITABLE_ARTIFACT_CHANGED"
                elif failure == "disk":
                    context = patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=0))
                    code = "EDITABLE_STORAGE_INSUFFICIENT"
                else:
                    context = patch.object(worker.os, "replace", side_effect=OSError("injected promotion failure"))
                    code = "EDITABLE_COPY_FAILED"
                with context, self.assertRaises(ProductionJobError) as error:
                    worker._copy_editable_artifact(source, target, expected_hash=expected)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(target.read_bytes(), b"previous valid artifact")
                self.assertEqual(list(root.glob("*.partial")), [])

    def test_worker_boundary_preserves_media_failure_and_requires_action_for_unknown_errors(self):
        cases = ((MediaAdapterError("MEDIA_PROBE_FAILED", "moov atom not found", retryable=True),
                  "MEDIA_PROBE_FAILED", 2),
                 (RuntimeError("unexpected probe failure"), "WORKER_UNHANDLED", 3))
        for error, code, expected in cases:
            with self.subTest(code=code):
                output = io.BytesIO()
                command = SimpleNamespace(payload={"args": {}}, job_id="corrupt-media", stage_id="local-file")
                with patch.object(worker, "_read_command", return_value=command), \
                     patch.object(worker.WorkerConfig, "from_args", return_value=SimpleNamespace()), \
                     patch.object(worker, "run_local_file", side_effect=error), \
                     patch.object(worker.sys, "stdout", SimpleNamespace(buffer=output)):
                    self.assertEqual(worker.main(), expected)
                messages = [Envelope.from_line(line) for line in output.getvalue().splitlines(keepends=True)]
                failures = [message for message in messages if message.message_type is MessageType.FAILURE]
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0].payload["code"], code)
                self.assertFalse(failures[0].payload["retryable"])
                self.assertEqual(failures[0].payload["condition"], getattr(error, "condition", str(error)))
                self.assertEqual(messages[-1].payload["status"], "failed")

    def test_start_command_preserves_voice_and_rejects_malformed_ids(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"fixture")
            args = {"job_id": "voice-test", "stage_id": "local-file", "source_path": str(source),
                "output_dir": str(root / "output"), "app_root": str(root), "model_root": str(root / "models"),
                "media_runtime_root": str(root), "ffmpeg_path": str(root / "ffmpeg.exe"), "ffprobe_path": str(root / "ffprobe.exe"),
                "enable_dubbing": True, "tts_voice_id": "vi-thuy-dung-vieneu3-v1"}
            self.assertEqual(WorkerConfig.from_args(args).tts_voice_id, args["tts_voice_id"])
            for invalid in (True, 123, "", "../escape", "a" * 97):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ProductionJobError, "COMMAND_INVALID"):
                    WorkerConfig.from_args({**args, "tts_voice_id": invalid})
            del args["tts_voice_id"]
            self.assertIsNone(WorkerConfig.from_args(args).tts_voice_id)

    def test_worker_runtime_keeps_third_party_packaging_ahead_of_app_policy_package(self) -> None:
        """The bundled Argos dependency must not resolve the repo package."""

        with TemporaryDirectory(prefix="dubflow-worker-site-packages-") as directory:
            site_packages = Path(directory)
            external_packaging = site_packages / "packaging"
            external_packaging.mkdir()
            (external_packaging / "__init__.py").write_text("__version__ = 'test-runtime'\n", encoding="utf-8")
            (external_packaging / "version.py").write_text("MARKER = 'bundled-third-party'\n", encoding="utf-8")
            repo_root = Path(__file__).resolve().parents[3]
            script = "import engine.dubflow.worker.production_job; import packaging.version as version; assert version.MARKER == 'bundled-third-party'"
            environment = os.environ.copy()
            environment["DUBFLOW_WORKER_PROCESS"] = "1"
            environment["PYTHONPATH"] = os.pathsep.join((str(repo_root), str(site_packages)))
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=repo_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_srt_and_vtt_sidecars_preserve_integer_millisecond_ticks(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-sidecar-") as directory:
            root = Path(directory)
            srt_video = root / "episode [spaces].mp4"
            srt_video.write_bytes(b"media")
            srt_video.with_suffix(".srt").write_text(
                "1\n00:00:01,005 --> 00:00:02,250\nHello\n",
                encoding="utf-8",
            )
            srt = _load_sidecar(srt_video)
            self.assertIsNotNone(srt)
            self.assertEqual((srt[0].start_ms, srt[0].end_ms), (1005, 2250))

            vtt_video = root / "clip.mp4"
            vtt_video.write_bytes(b"media")
            vtt_video.with_suffix(".vtt").write_text(
                "WEBVTT\n\n00:01.500 --> 00:02.750\nXin chao\n",
                encoding="utf-8",
            )
            vtt = _load_sidecar(vtt_video)
            self.assertIsNotNone(vtt)
            self.assertEqual((vtt[0].start_ms, vtt[0].end_ms), (1500, 2750))

    def test_subtitle_outputs_are_atomic_and_editable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-subtitle-") as directory:
            root = Path(directory)
            srt, ass = _write_subtitles(
                root,
                (TextCue("cue-1", 0, 1250, "Hello", "Xin chào"),),
            )
            self.assertEqual(srt.read_text(encoding="utf-8").splitlines()[2], "Xin chào")
            self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.25", ass.read_text(encoding="utf-8"))
            self.assertFalse(list(root.glob("*.partial")))

    def test_worker_rejects_system_media_executable(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-security-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"media")
            app = root / "app"
            app.mkdir()
            media = root / "media"
            media.mkdir()
            ffmpeg = media / "ffmpeg.exe"
            ffprobe = media / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            with self.assertRaisesRegex(ProductionJobError, "RUNTIME_ROOT_UNSAFE"):
                WorkerConfig.from_args(
                    {
                        "job_id": "job-1",
                        "stage_id": "local-file",
                        "source_path": str(source),
                        "output_dir": str(root / "output"),
                        "app_root": str(app),
                        "model_root": str(root / "models"),
                        "media_runtime_root": str(media),
                        "ffmpeg_path": str(root / "outside-ffmpeg.exe"),
                        "ffprobe_path": str(ffprobe),
                    }
                )

    def test_qc_rejects_render_that_drops_source_audio(self) -> None:
        with TemporaryDirectory(prefix="dubflow-worker-qc-") as directory:
            source_path = Path(directory) / "source.mp4"
            source_path.write_bytes(b"media")
            source_probe = parse_ffprobe_json(
                json.dumps({
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "codec_name": "h264",
                            "time_base": "1/1000",
                            "start_pts": 0,
                            "duration_ts": 1000,
                        },
                        {
                            "index": 1,
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "time_base": "1/48000",
                            "start_pts": 0,
                            "duration_ts": 48000,
                        },
                    ],
                    "format": {"duration_ts": 1000, "time_base": "1/1000"},
                }),
                source_path=source_path,
            )
            output_probe = parse_ffprobe_json(
                json.dumps({
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "codec_name": "h264",
                            "time_base": "1/1000",
                            "start_pts": 0,
                            "duration_ts": 1000,
                        }
                    ],
                    "format": {"duration_ts": 1000, "time_base": "1/1000"},
                }),
                source_path=Path(directory) / "output.mp4",
            )
            with self.assertRaisesRegex(ProductionJobError, "QC_AUDIO_MISSING"):
                _validate_rendered_audio(source_probe, output_probe)


if __name__ == "__main__":
    unittest.main()

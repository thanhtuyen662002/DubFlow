from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from engine.dubflow.media import FfmpegMediaAdapter, MediaAdapterError
from engine.dubflow.media.hardware import HardwareRenderAdapter
from engine.dubflow.models.hardware import ExecutionProfile, HardwareResolver, HardwareSnapshot, NvidiaHardwareProbe
from engine.dubflow.worker import production_job as worker


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory(prefix="dubflow-render-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.runtime = self.root / "app runtime"
        self.runtime.mkdir()
        self.ffmpeg = self.runtime / "ffmpeg.exe"
        self.ffmpeg.write_bytes(b"MZ fixture")
        self.ffmpeg.chmod(0o755)
        self.ffprobe = self.runtime / "ffprobe.exe"
        self.ffprobe.write_bytes(b"MZ fixture")
        self.ffprobe.chmod(0o755)
        self.source = self.root / "nguồn video.mp4"
        self.source.write_bytes(b"source fixture")
        self.audio = self.root / "giọng Việt.wav"
        self.audio.write_bytes(b"audio fixture")
        self.output = self.root / "output.mp4"
        self.resolver = Mock(spec=HardwareResolver)
        self.resolver.resolve.return_value = ExecutionProfile("gpu", "gpu", "h264_nvenc", True, 8192, "fixture", False)
        self.media = Mock(spec=FfmpegMediaAdapter)
        self.media.render.return_value = self.output

    def adapter(self, **options) -> HardwareRenderAdapter:
        return HardwareRenderAdapter(self.media, resolver=self.resolver, requested="gpu", **options)


class RenderFallbackTests(Fixture):
    def test_gpu_success_is_one_attempt_with_selected_audio(self) -> None:
        renderer = self.adapter()
        self.assertEqual(renderer.render(self.source, self.output, audio_path=self.audio, preserve_original_audio=False), self.output)
        self.assertEqual(self.media.render.call_count, 1)
        self.assertEqual(self.media.render.call_args.kwargs["video_encoder"], "h264_nvenc")
        self.assertEqual(self.media.render.call_args.kwargs["audio_path"], self.audio)
        self.assertEqual(renderer.evidence()["status"], "command_succeeded")
        self.assertEqual(renderer.evidence()["render_attempts"], 1)

    def test_gpu_failure_preserves_audio_and_subtitles_on_cpu_retry(self) -> None:
        for code, retryable in (("MEDIA_COMMAND_FAILED", True), ("MEDIA_COMMAND_TIMEOUT", True), ("MEDIA_OUTPUT_INVALID", False)):
            with self.subTest(code=code):
                media = Mock(spec=FfmpegMediaAdapter)
                media.render.side_effect = [MediaAdapterError(code, "fixture failure", retryable=retryable), self.output]
                retire = Mock()
                renderer = HardwareRenderAdapter(media, resolver=self.resolver, requested="gpu", on_gpu_retired=retire)
                subtitle = self.root / "captions.ass"
                renderer.render(self.source, self.output, audio_path=self.audio, subtitle_path=subtitle,
                                preserve_original_audio=False, burn_in_subtitles=True, overwrite=True)
                first, second = media.render.call_args_list
                expected = dict(first.kwargs)
                self.assertEqual(expected.pop("video_encoder"), "h264_nvenc")
                self.assertEqual(second.kwargs, expected)
                self.assertEqual(second.args, first.args)
                self.assertEqual(renderer.evidence()["selected"], "cpu")
                self.assertEqual(renderer.evidence()["render_attempts"], 2)
                retire.assert_called_once_with(code)

    def test_gpu_is_not_retried_on_later_render_in_same_session(self) -> None:
        self.media.render.side_effect = [MediaAdapterError("MEDIA_COMMAND_FAILED", "OOM", retryable=True), self.output, self.output]
        renderer = self.adapter()
        renderer.render(self.source, self.output, audio_path=self.audio, preserve_original_audio=False)
        renderer.render(self.source, self.output)
        self.assertEqual(self.media.render.call_count, 3)
        self.assertEqual(sum("video_encoder" in call.kwargs for call in self.media.render.call_args_list), 1)
        self.resolver.resolve.assert_called_once()
        self.assertEqual(len(renderer.warnings), 1)

    def test_cpu_failure_propagates_after_one_gpu_fallback(self) -> None:
        failure = MediaAdapterError("MEDIA_OUTPUT_FAILED", "disk full", retryable=True)
        self.media.render.side_effect = [MediaAdapterError("MEDIA_COMMAND_FAILED", "OOM", retryable=True), failure]
        renderer = self.adapter()
        with self.assertRaises(MediaAdapterError) as caught:
            renderer.render(self.source, self.output)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.media.render.call_count, 2)
        self.assertEqual(renderer.evidence()["status"], "command_failed")

    def test_cancel_storage_startup_and_invalid_input_do_not_retry_gpu(self) -> None:
        for code, retryable in (("MEDIA_CANCELLED", False), ("MEDIA_OUTPUT_FAILED", True),
                                ("MEDIA_COMMAND_START_FAILED", True), ("MEDIA_AUDIO_MISSING", False),
                                ("MEDIA_COMMAND_FAILED", False)):
            with self.subTest(code=code):
                self.media.reset_mock()
                self.media.render.side_effect = MediaAdapterError(code, "fixture failure", retryable=retryable)
                retired = Mock()
                renderer = self.adapter(on_gpu_retired=retired)
                with self.assertRaises(MediaAdapterError):
                    renderer.render(self.source, self.output)
                self.assertEqual(self.media.render.call_count, 1)
                retired.assert_not_called()

    def test_persistence_failure_stops_before_cpu_attempt(self) -> None:
        self.media.render.side_effect = MediaAdapterError("MEDIA_COMMAND_FAILED", "OOM", retryable=True)
        renderer = self.adapter(on_gpu_retired=Mock(side_effect=OSError("checkpoint unavailable")))
        with self.assertRaises(OSError):
            renderer.render(self.source, self.output)
        self.assertEqual(self.media.render.call_count, 1)

    def test_unused_renderer_never_probes_and_never_claims_success(self) -> None:
        renderer = self.adapter()
        self.assertEqual(renderer.evidence()["status"], "not_rendered")
        self.assertIsNone(renderer.evidence()["selected"])
        self.resolver.resolve.assert_not_called()
        self.media.render.assert_not_called()

    def test_restored_retirement_skips_probe_and_gpu(self) -> None:
        renderer = self.adapter(retired_reason="retired for job")
        self.assertEqual(renderer.evidence()["status"], "not_rendered")
        renderer.render(self.source, self.output)
        self.resolver.resolve.assert_not_called()
        self.assertNotIn("video_encoder", self.media.render.call_args.kwargs)
        self.assertEqual(renderer.evidence()["selected"], "cpu")

    def test_invalid_profile_is_rejected_before_io(self) -> None:
        for value in (None, True, "GPU", "unknown", []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HardwareRenderAdapter(self.media, resolver=self.resolver, requested=value)
        self.media.render.assert_not_called()


class MediaEncoderTests(Fixture):
    def owned_media(self):
        commands = []
        def runner(argv, timeout):
            commands.append(argv)
            Path(argv[-1]).write_bytes(b"encoded fixture")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return FfmpegMediaAdapter(self.ffmpeg, trusted_root=self.runtime, runner=runner), commands

    def test_default_keeps_lgpl_software_encoder(self) -> None:
        media, commands = self.owned_media()
        media.render(self.source, self.output)
        command = commands[0]
        self.assertEqual(command[command.index("-c:v") + 1], "h264_mf")
        self.assertEqual(command[command.index("-quality") + 1], "90")
        self.assertNotIn("libx264", command)
        self.assertNotIn("-gpu", command)

    def test_nvenc_targets_probe_device_and_keeps_dubbed_audio(self) -> None:
        media, commands = self.owned_media()
        media.render(self.source, self.output, audio_path=self.audio, preserve_original_audio=False, video_encoder="h264_nvenc")
        command = commands[0]
        self.assertEqual(command[command.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(command[command.index("-gpu") + 1], "0")
        self.assertIn(str(self.audio.resolve()), command)
        self.assertIn("1:a:0", command)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")

    def test_encoder_is_closed_before_process_start(self) -> None:
        media, commands = self.owned_media()
        for value in (None, [], "libx264", "h264_mf", "h264_nvenc -gpu 1"):
            with self.subTest(value=value), self.assertRaises(MediaAdapterError) as caught:
                media.render(self.source, self.output, video_encoder=value)
            self.assertEqual(caught.exception.code, "MEDIA_ENCODER_INVALID")
        self.assertEqual(commands, [])


class WorkerRenderPolicyTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.root / ".dubflow-work"
        self.work.mkdir()
        self.checkpoint_path = self.work / "checkpoint.json"
        self.source_hash = worker._sha256(self.source)
        self.checkpoint = {"schema_version": 1, "source_hash": self.source_hash, "stages": {}}
        self.config = worker.WorkerConfig("job-1", "stage-1", self.source, self.root / "out", self.root,
                                          self.root / "models", self.runtime, self.ffmpeg, self.runtime / "ffprobe.exe",
                                          render_profile="gpu")
        self.emitter = Mock()
        self.warnings = []
        self.probe = Mock(spec=NvidiaHardwareProbe)
        self.probe.detect.return_value = HardwareSnapshot(1, True, "Fixture GPU", 8192, "h264_nvenc", "fixture")

    def builder(self, config=None):
        return worker._hardware_renderer(config or self.config, self.media, self.work, self.checkpoint,
                                         self.checkpoint_path, self.source_hash, self.emitter, self.warnings)

    def install_policy(self, **changes):
        policy = {"schema_version": 1, "producer_contract": "hardware-render-policy-v1", "job_id": self.config.job_id,
                  "source_hash": self.source_hash, "selected": "cpu", "encoder": "software", "failure_code": "MEDIA_COMMAND_FAILED"}
        policy.update(changes)
        path = self.work / "render-policy.json"
        worker._atomic_json(path, policy)
        worker._write_stage(self.checkpoint, self.checkpoint_path, "render_policy", {"path": str(path), "sha256": worker._sha256(path)})
        return path

    def test_cpu_default_does_not_locate_or_probe_driver(self) -> None:
        with patch.object(NvidiaHardwareProbe, "for_system") as factory:
            renderer = self.builder(replace(self.config, render_profile="cpu"))
            renderer.render(self.source, self.output)
        factory.assert_not_called()
        self.assertNotIn("video_encoder", self.media.render.call_args.kwargs)

    def test_gpu_retirement_is_saved_before_cpu_attempt(self) -> None:
        def attempt(*args, **options):
            if "video_encoder" in options:
                raise MediaAdapterError("MEDIA_COMMAND_FAILED", "OOM", retryable=True)
            path = self.work / "render-policy.json"
            self.assertTrue(path.is_file())
            self.assertEqual(self.checkpoint["stages"]["render_policy"]["sha256"], worker._sha256(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["job_id"], self.config.job_id)
            return self.output
        self.media.render.side_effect = attempt
        with patch.object(NvidiaHardwareProbe, "for_system", return_value=self.probe):
            self.builder().render(self.source, self.output, audio_path=self.audio, preserve_original_audio=False)
        self.emitter.checkpoint.assert_called_once()
        self.assertEqual(self.media.render.call_count, 2)

    def test_resumed_job_never_retries_retired_gpu(self) -> None:
        self.install_policy()
        reloaded = worker._read_checkpoint(self.checkpoint_path, self.source_hash)
        self.checkpoint = reloaded
        with patch.object(NvidiaHardwareProbe, "for_system") as factory:
            renderer = self.builder()
            renderer.render(self.source, self.output)
        factory.assert_not_called()
        self.assertNotIn("video_encoder", self.media.render.call_args.kwargs)
        self.assertEqual(self.warnings, [])

    def test_wrong_job_source_version_or_selection_is_safe_cpu_data(self) -> None:
        for changes in ({"job_id": "other"}, {"source_hash": "other"}, {"schema_version": 2}, {"schema_version": True},
                        {"producer_contract": "unknown"}, {"selected": "gpu"}, {"encoder": "h264_nvenc"}, {"failure_code": []}):
            with self.subTest(changes=changes):
                self.install_policy(**changes)
                self.warnings.clear()
                with patch.object(NvidiaHardwareProbe, "for_system") as factory:
                    self.builder().render(self.source, self.output)
                factory.assert_not_called()
                self.assertEqual(self.warnings, ["GPU_POLICY_UNVERIFIED: software encoder selected"])

    def test_missing_corrupt_oversized_or_changed_policy_cannot_enable_gpu(self) -> None:
        for mode in ("missing", "corrupt", "oversized", "changed"):
            with self.subTest(mode=mode):
                path = self.install_policy()
                if mode == "missing":
                    path.unlink()
                elif mode == "corrupt":
                    path.write_bytes(b"{not json}")
                elif mode == "oversized":
                    path.write_bytes(b"x" * 65537)
                else:
                    path.write_bytes(path.read_bytes() + b" ")
                self.warnings.clear()
                with patch.object(NvidiaHardwareProbe, "for_system") as factory:
                    self.builder().render(self.source, self.output)
                factory.assert_not_called()
                self.assertTrue(self.warnings)

    def test_orphaned_policy_after_hard_kill_keeps_cpu_without_probe(self) -> None:
        self.install_policy()
        self.checkpoint["stages"].pop("render_policy")
        worker._atomic_json(self.checkpoint_path, self.checkpoint)
        self.checkpoint = worker._read_checkpoint(self.checkpoint_path, self.source_hash)
        with patch.object(NvidiaHardwareProbe, "for_system") as factory:
            self.builder().render(self.source, self.output)
        factory.assert_not_called()
        self.assertNotIn("video_encoder", self.media.render.call_args.kwargs)
        self.assertEqual(self.warnings, ["GPU_POLICY_UNVERIFIED: software encoder selected"])

    def test_os_probe_lookup_failure_preserves_usable_cpu_path(self) -> None:
        with patch.object(NvidiaHardwareProbe, "for_system", side_effect=OSError("lookup failed")):
            self.builder().render(self.source, self.output)
        self.assertNotIn("video_encoder", self.media.render.call_args.kwargs)
        self.assertIn("GPU_PROBE_UNAVAILABLE: software encoder selected", self.warnings)

    def test_cache_evidence_does_not_probe_or_invent_hardware_health(self) -> None:
        self.output.write_bytes(b"cached video fixture")
        recorded = {"selected": "gpu", "encoder": "h264_nvenc", "status": "command_succeeded"}
        self.checkpoint["stages"]["render"] = {"status": "completed", "sha256": worker._sha256(self.output), "render_profile": recorded}
        renderer = HardwareRenderAdapter(self.media, resolver=self.resolver, requested="gpu")
        report = worker._hardware_render_evidence(self.config, renderer, self.checkpoint, "render", self.output)
        self.assertEqual(report["evidence"]["status"], "reused_checkpoint")
        self.assertEqual(report["evidence"]["recorded"], recorded)
        self.resolver.resolve.assert_not_called()
        self.output.write_bytes(b"changed video fixture")
        report = worker._hardware_render_evidence(self.config, renderer, self.checkpoint, "render", self.output)
        self.assertIsNone(report["evidence"]["recorded"])

    def test_command_profile_is_optional_and_closed(self) -> None:
        names = ("job_id", "stage_id", "source_path", "output_dir", "app_root", "model_root", "media_runtime_root", "ffmpeg_path", "ffprobe_path")
        args = {name: str(getattr(self.config, name)) for name in names}
        self.assertEqual(worker.WorkerConfig.from_args(args).render_profile, "cpu")
        for value in (None, True, "unknown", [], "GPU"):
            with self.subTest(value=value), self.assertRaises(worker.ProductionJobError) as caught:
                worker.WorkerConfig.from_args({**args, "render_profile": value})
            self.assertEqual(caught.exception.code, "COMMAND_INVALID")


if __name__ == "__main__":
    unittest.main()

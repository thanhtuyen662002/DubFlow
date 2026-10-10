from __future__ import annotations

from hashlib import sha256
from contextlib import closing
import importlib.util
import json
import hashlib
import io
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from types import SimpleNamespace

from packaging.release.bootstrap import BootstrapInstallError, install_bundle
from packaging.release.builder import BuildError, build_bundle, main as builder_main
from packaging.release.manifest import ManifestError, ReleaseArtifact, ReleaseManifest, hash_file, load_manifest
from scripts.release import production_smoke


SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
SOURCE_DATE_EPOCH = 1_700_000_000


def _runtime(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "python.exe").write_bytes(b"portable-python-fixture")
    (root / "python312.dll").write_bytes(b"portable-python-dll")
    (root / "Lib").mkdir()
    (root / "Lib" / "site.py").write_text("# fixture\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")


def _desktop_binary(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    binary = root / "DubFlow.exe"
    binary.write_bytes(b"MZ-dubflow-host-fixture")
    return binary


class ReleaseBundleTests(unittest.TestCase):
    def test_builder_stdout_preserves_vietnamese_on_a_windows_legacy_console(self) -> None:
        stream = io.BytesIO()
        console = io.TextIOWrapper(stream, encoding="cp1252", write_through=True)
        manifest = {"voice": "Ngọc Huyền", "path": "D:/Thư viện/video.mp4"}
        result = SimpleNamespace(bundle_path=Path("DubFlow.zip"), checksum_path=Path("SHA256.txt"), manifest=SimpleNamespace(to_dict=lambda: manifest))
        with mock.patch("packaging.release.builder.build_bundle", return_value=result), mock.patch("sys.stdout", console):
            self.assertEqual(builder_main(["--source-root", ".", "--output-dir", "out", "--version", "test", "--source-sha", SOURCE_SHA, "--runtime-root", "runtime"]), 0)
        decoded = json.loads(stream.getvalue().decode("cp1252"))
        self.assertEqual(decoded["manifest"], manifest)
        console.detach()

    def test_build_is_deterministic_and_manifest_hashes_every_payload_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            output_a = root / "a"
            output_b = root / "b"
            result_a = build_bundle(
                source_root=".",
                output_dir=output_a,
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            result_b = build_bundle(
                source_root=".",
                output_dir=output_b,
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            self.assertEqual(result_a.bundle_path.read_bytes(), result_b.bundle_path.read_bytes())
            self.assertEqual(result_a.checksum_path.read_text(encoding="utf-8"), result_b.checksum_path.read_text(encoding="utf-8"))
            manifest = load_manifest(result_a.staging_dir / "release-manifest.json")
            self.assertFalse(manifest.signature_required)
            self.assertEqual(manifest.release_channel, "candidate")
            for artifact in manifest.artifacts:
                path = result_a.staging_dir / artifact.path
                self.assertEqual(path.stat().st_size, artifact.size_bytes)
                self.assertEqual(sha256(path.read_bytes()).hexdigest(), artifact.sha256)
            self.assertFalse((result_a.staging_dir / "runtime" / "__pycache__").exists())

    def test_generated_cmd_keeps_bundle_root_quote_terminated(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            setup = (result.staging_dir / "setup.cmd").read_text(encoding="utf-8")
            self.assertIn('set "BUNDLE_ROOT=%~dp0."', setup)
            self.assertIn('"%BUNDLE_ROOT%\\runtime\\python.exe"', setup)
            self.assertIn('"%BUNDLE_ROOT%\\runtime\\python.exe" -B', setup)
            self.assertIn('"%BUNDLE_ROOT%\\app\\packaging\\release\\bootstrap.py"', setup)
            self.assertIn('--bundle-root "%BUNDLE_ROOT%" --install-root "%LOCALAPPDATA%\\DubFlow"', setup)
            self.assertIn('start "" /b "%LOCALAPPDATA%\\DubFlow\\DubFlow.cmd"', setup)
            self.assertNotIn('--bundle-root "%~dp0%"', setup)

    def test_install_verifies_then_activates_versioned_pointer_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "DubFlow App Data"
            report = install_bundle(result.staging_dir, install_root)
            self.assertTrue(report["ready"])
            self.assertEqual(report["version"], "0.1.0-rc1")
            self.assertTrue((install_root / "current.json").is_file())
            self.assertTrue((install_root / "DubFlow.cmd").is_file())
            second = install_bundle(result.staging_dir, install_root)
            self.assertEqual(second["manifest_sha256"], report["manifest_sha256"])
            self.assertEqual(json.loads((install_root / "current.json").read_text(encoding="utf-8"))["current_version"], "0.1.0-rc1")
            (install_root / "versions" / "0.1.0-rc1" / "runtime" / "python.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(BootstrapInstallError, "INSTALLED_VERSION_INVALID"):
                install_bundle(result.staging_dir, install_root)

    def test_corruption_is_rejected_before_destination_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            target = result.staging_dir / "setup.cmd"
            target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
            install_root = root / "install"
            with self.assertRaisesRegex(BootstrapInstallError, "SIZE_MISMATCH|HASH_MISMATCH"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())

    def test_manifest_wire_format_preserves_large_u64_as_decimal_string(self) -> None:
        artifact = ReleaseArtifact("large", "payload/large.bin", 2**53 + 1, "a" * 64)
        value = artifact.to_dict()
        self.assertEqual(value["size_bytes"], str(2**53 + 1))
        manifest = ReleaseManifest(
            "0.1.0-rc1",
            SOURCE_SHA,
            "3.12.10",
            "v1",
            "windows",
            "x86_64",
            "2023-11-14T22:13:20Z",
            "candidate",
            False,
            "none",
            "unsigned-candidate",
            "",
            (artifact,),
        )
        decoded = ReleaseManifest.from_mapping(manifest.to_dict())
        self.assertEqual(decoded.artifacts[0].size_bytes, 2**53 + 1)

    def test_unsafe_paths_and_stable_unsigned_release_fail_closed(self) -> None:
        for unsafe_path in ("../outside", "app/file:stream", "app/CON.txt", "app/file. ", "app\\file"):
            with self.assertRaises(ManifestError):
                ReleaseArtifact("escape", unsafe_path, 1, "a" * 64)
        with self.assertRaises(ManifestError):
            ReleaseManifest(
                "0.1.0-rc1",
                SOURCE_SHA,
                "3.12.10",
                "v1",
                "windows",
                "x86_64",
                "2023-11-14T22:13:20Z",
                "candidate",
                False,
                "none",
                "unsigned-candidate",
                "",
                (
                    ReleaseArtifact("one", "app/Foo", 0, "a" * 64),
                    ReleaseArtifact("two", "app/foo", 0, "b" * 64),
                ),
            )
        with self.assertRaises(BuildError):
            build_bundle(
                source_root=".",
                output_dir=Path("tests") / "release" / "_out",
                version="0.1.0",
                source_sha=SOURCE_SHA,
                runtime_root=Path("tests") / "release" / "missing-runtime",
                release_channel="stable",
            )

    def test_stable_signature_metadata_fails_closed_even_when_forged(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            manifest_path = result.staging_dir / "release-manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["release_channel"] = "stable"
            payload["security"] = {
                "signature_required": True,
                "algorithm": "ed25519",
                "key_id": "attacker-key",
                "value": "forged-signature",
            }
            manifest_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(BootstrapInstallError, "SIGNATURE_TRUST"):
                install_bundle(result.staging_dir, root / "install")

    def test_unmanifested_file_is_rejected_before_destination_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            (result.staging_dir / "evil.bin").write_bytes(b"not in manifest")
            install_root = root / "install"
            with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())

    def test_runtime_bytecode_cache_is_transient_and_does_not_block_install(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            cache = result.staging_dir / "app" / "packaging" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "generated.cpython-312.pyc").write_bytes(b"transient")
            report = install_bundle(result.staging_dir, root / "install")
            self.assertTrue(report["ready"])

    def test_install_resumes_deterministic_staging_and_removes_progress(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "install"
            staging = install_root / "versions" / ".0.1.0-rc1.staging"
            staging.mkdir(parents=True)
            shutil.copy2(result.staging_dir / "setup.cmd", staging / "setup.cmd")
            manifest_hash = hash_file(result.staging_dir / "release-manifest.json")
            (install_root / "install-progress-0.1.0-rc1.json").parent.mkdir(parents=True, exist_ok=True)
            (install_root / "install-progress-0.1.0-rc1.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "failed",
                        "version": "0.1.0-rc1",
                        "manifest_sha256": manifest_hash,
                        "staging_path": str(staging),
                        "completed": ["setup.cmd"],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            report = install_bundle(result.staging_dir, install_root)
            self.assertTrue(report["ready"])
            self.assertTrue((install_root / "versions" / "0.1.0-rc1").is_dir())
            self.assertFalse(staging.exists())
            self.assertFalse((install_root / "install-progress-0.1.0-rc1.json").exists())

    def test_copy_failure_leaves_actionable_failed_progress_and_staging(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "install"
            with mock.patch("packaging.release.bootstrap.shutil.copy2", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(BootstrapInstallError, "STAGING_COPY_FAILED"):
                    install_bundle(result.staging_dir, install_root)
            progress = json.loads((install_root / "install-progress-0.1.0-rc1.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "failed")
            self.assertIn("STAGING_COPY_FAILED", progress["error"])
            self.assertTrue((install_root / "versions" / ".0.1.0-rc1.staging").is_dir())
            self.assertFalse((install_root / "current.json").exists())

    def test_staged_reverification_rejects_extra_file_and_preserves_failure_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "install"
            staging = install_root / "versions" / ".0.1.0-rc1.staging"
            staging.parent.mkdir(parents=True)
            shutil.copytree(result.staging_dir, staging)
            (staging / "evil.bin").write_bytes(b"unmanifested")
            with self.assertRaisesRegex(BootstrapInstallError, "UNMANIFESTED_ARTIFACT"):
                install_bundle(result.staging_dir, install_root)
            self.assertFalse((install_root / "current.json").exists())
            progress = json.loads((install_root / "install-progress-0.1.0-rc1.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["status"], "failed")
            self.assertTrue(staging.is_dir())

    def test_launcher_rejects_tampered_pointer_and_candidate_qualification(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            install_root = root / "install"
            install_bundle(result.staging_dir, install_root)
            launcher_path = install_root / "versions" / "0.1.0-rc1" / "app" / "packaging" / "release" / "launcher.py"
            spec = importlib.util.spec_from_file_location("installed_dubflow_launcher", launcher_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            launcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher)
            self.assertTrue(launcher.self_check()["ready"])

            current_path = install_root / "current.json"
            current = json.loads(current_path.read_text(encoding="utf-8"))
            current["version_path"] = "versions/other"
            current_path.write_text(json.dumps(current), encoding="utf-8")
            self.assertEqual(launcher.self_check()["code"], "CURRENT_POINTER_MISMATCH")
            current["version_path"] = "versions/0.1.0-rc1"
            current_path.write_text(json.dumps(current), encoding="utf-8")

            status_path = install_root / "versions" / "0.1.0-rc1" / "release-status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["production_qualified"] = True
            status_path.write_text(json.dumps(status), encoding="utf-8")
            self.assertEqual(launcher.self_check()["code"], "STATUS_TAMPERED")

    def test_desktop_host_is_required_for_release_and_verified_before_launch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            host = _desktop_binary(root / "host")
            result = build_bundle(
                source_root=".",
                output_dir=root / "out",
                version="0.1.0-rc1",
                source_sha=SOURCE_SHA,
                runtime_root=runtime,
                desktop_binary=host,
                require_desktop_host=True,
                source_date_epoch=SOURCE_DATE_EPOCH,
            )
            packaged = result.staging_dir / "app" / "bin" / "DubFlow.exe"
            self.assertEqual(packaged.read_bytes(), host.read_bytes())
            artifact = next(item for item in result.manifest.artifacts if item.path == "app/bin/DubFlow.exe")
            self.assertTrue(artifact.executable)

            install_root = root / "install"
            install_bundle(result.staging_dir, install_root)
            launcher_path = install_root / "versions" / "0.1.0-rc1" / "app" / "packaging" / "release" / "launcher.py"
            spec = importlib.util.spec_from_file_location("installed_dubflow_launcher_host", launcher_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            launcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher)
            process = mock.Mock(pid=4242)
            with mock.patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
                self.assertEqual(launcher.main([]), 0)
            popen.assert_called_once()
            installed_host = install_root / "versions" / "0.1.0-rc1" / "app" / "bin" / "DubFlow.exe"
            self.assertEqual(Path(popen.call_args.args[0][0]).resolve(), installed_host.resolve())

            installed_host.write_bytes(b"tampered")
            with mock.patch.object(launcher.subprocess, "Popen") as popen:
                self.assertEqual(launcher.main([]), 3)
            popen.assert_not_called()

    def test_required_desktop_host_missing_fails_build(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            _runtime(runtime)
            with self.assertRaisesRegex(BuildError, "desktop host binary is required"):
                build_bundle(
                    source_root=".",
                    output_dir=root / "out",
                    version="0.1.0-rc1",
                    source_sha=SOURCE_SHA,
                    runtime_root=runtime,
                    require_desktop_host=True,
                    source_date_epoch=SOURCE_DATE_EPOCH,
                )

    def test_setup_bootstrap_is_user_owned_and_embeds_the_release_payload(self) -> None:
        source = Path("packaging/release/windows_setup.rs").read_text(encoding="utf-8")
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('include_bytes!(env!("DUBFLOW_PAYLOAD"))', source)
        self.assertIn("Expand-Archive", source)
        self.assertIn("drop(output)", source)
        self.assertIn("system_binary", source)
        self.assertIn('"System32"', source)
        self.assertIn("fs::create_dir(&root)", source)
        self.assertIn('.current_dir(&extracted)', source)
        self.assertIn('"setup.cmd"', source)
        self.assertNotIn("requireAdministrator", source)
        self.assertNotIn("7z.sfx", workflow)
        self.assertIn("target-feature=+crt-static", workflow)
        self.assertIn("actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020", workflow)
        self.assertIn("npm run tauri:build --prefix apps/desktop", workflow)
        self.assertIn("--desktop-binary", workflow)
        self.assertIn("--require-desktop-host", workflow)
        self.assertIn("Smoke install and launch desktop host", workflow)
        self.assertIn("launch_smoke = 'passed'", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("windows-x64.zip.sha256", workflow)
        self.assertIn('--target "$SOURCE_SHA"', workflow)
        self.assertIn('gh release create "$tag" --repo "$GITHUB_REPOSITORY"', workflow)
        self.assertIn('gh release upload "$tag" --repo "$GITHUB_REPOSITORY"', workflow)
        self.assertIn("resolved_sha", workflow)


class VoiceVersionQualificationTests(unittest.TestCase):
    def test_corrupt_media_qualification_rejects_untyped_or_repeated_failure(self):
        failure = {"event": "failed", "job_id": "smoke-corrupt", "code": "MEDIA_PROBE_FAILED",
                   "attempt": 1, "retryable": False}
        self.assertEqual(production_smoke._verify_corrupt_failure(json.dumps(failure), "smoke-corrupt")["attempt"], 1)
        invalid = ({**failure, "code": "WORKER_UNHANDLED"}, {**failure, "attempt": 3},
                   {**failure, "retryable": True})
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(production_smoke.SmokeError):
                production_smoke._verify_corrupt_failure(json.dumps(event), "smoke-corrupt")
        retry = {"event": "retrying", "job_id": "smoke-corrupt", "attempt": 1}
        with self.assertRaises(production_smoke.SmokeError):
            production_smoke._verify_corrupt_failure(json.dumps(retry) + "\n" + json.dumps(failure), "smoke-corrupt")

    def test_output_qualification_requires_streaming_producer_and_actual_pcm_hashes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            editable = root / "editable"
            editable.mkdir()
            for relative in ("final_vi.mp4", "captions_vi.srt", "captions_vi.ass", "qc_report.json", "editable/timeline.json"):
                (root / relative).write_bytes(b"qualification-fixture")
            artifacts = {}
            for key, name in (("original_audio", "source_audio.wav"), ("dialogue_stem", "dialogue_stem.wav"),
                              ("final_mix", "final_mix.wav")):
                path = editable / name
                with production_smoke.wave.open(str(path), "wb") as writer:
                    writer.setparams((2, 2, 16000, 16000, "NONE", "not compressed"))
                    writer.writeframes(b"\x00\x01" * 32000)
                digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                artifacts[key] = {"path": str(path), "content_hash": digest, "frame_count": 16000, "channels": 2,
                                  "sample_rate": 16000, "metrics": {"content_hash": digest, "clipped_samples": 0}}
            provenance = {"backend_id": "pcm-stream-duck-v1", "producer_version": "2.0.1",
                          "runtime": "owned-python/numpy-2.2.6", "non_destructive": True,
                          "source_hash": artifacts["original_audio"]["content_hash"]}
            mix_path = root / "mix_document.json"
            tts_path = root / "tts_document.json"
            mix = {**artifacts, "provenance": provenance}
            mix_path.write_text(json.dumps(mix))
            tts_root = root / "tts"
            from dataclasses import replace
            from engine.dubflow.asr import TimeBase, TimePoint
            from engine.dubflow.tts import LocalTtsAdapter, TtsConfig, TtsInput, TtsProvenance, DeterministicFixtureEngine, approved_default_voice
            from engine.dubflow.worker.tts_checkpoints import TtsCheckpointStore
            from engine.dubflow.worker.b2_audio import _dubbing_windows, _input_hash
            canonical = [{"cue_id": "cue-1", "start_ms": 0, "end_ms": 1000, "source_text": "Hello", "translated_text": "Xin chào", "confidence": 1.0}]
            base = TimeBase(1,1000)
            placement = {"schema_version": 1, "kind": "dubbing_placement", "recipe": "source-intercue-postroll-2000ms-gap120ms-v1",
                "time_base": base.to_dict(), "source_end": TimePoint(1000,base).to_dict(),
                "source_audio_sha256": artifacts["original_audio"]["content_hash"], "canonical_cue_input_hash": _input_hash(canonical),
                "gap_evidence": "recognized-source-cue-intervals;silence-not-certified", "windows": _dubbing_windows(canonical,1000)}
            placement_bytes = (json.dumps(placement,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
            placement_digest = "sha256:" + hashlib.sha256(placement_bytes).hexdigest()
            (root/'dubbing_placement.json').write_bytes(placement_bytes)
            (editable/'dubbing_placement.json').write_bytes(placement_bytes)
            config = TtsConfig(max_attempts=1)
            voice = replace(approved_default_voice(),voice_id='vi-truc-ly-vieneu3-v1')
            tts_provenance = TtsProvenance('qualification-fixture','3.3.0','vieneu-v3-turbo-onnx-v1','fixture',
                'timeline-v1',config.content_hash(),placement_digest,voice.model_id,voice.model_version,voice.model_hash,
                voice.content_hash(),voice.voice_id,voice.voice_version,config.requested_profile,'fixture',config.resource)
            cue = TtsInput('cue-1','cue-1','Xin chào',TimePoint(0,base),TimePoint(1000,base),render_window_end=TimePoint(1000,base))
            store = TtsCheckpointStore(tts_root,identity='2'*64)
            speech = LocalTtsAdapter(DeterministicFixtureEngine(),config=config,voice=voice,provenance=tts_provenance,
                output_dir=tts_root).synthesize((cue,),input_hash=placement_digest,on_checkpoint=store.commit)
            tts_artifact = speech.artifacts[0].to_dict()
            record = tts_root / "checkpoints" / (hashlib.sha256(b"cue-1").hexdigest() + ".json")
            tts_path.write_bytes(speech.to_bytes())
            manifest = {"audio": {"mode": "dubbed", "backend": "vieneu-v3-turbo-onnx-v1", "tts_document": str(tts_path),
                                   "mix_document": str(mix_path), "mix_provenance": provenance,
                                   "dubbing_placement": {"schema_version": 1, "path": str(root/'dubbing_placement.json'), "sha256": placement_digest}},
                        "cues": canonical,"warnings": []}
            (root / "job_manifest.json").write_text(json.dumps(manifest))
            probe = {"streams": [{"codec_type": "video", "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac", "channels": 2}],
                     "format": {"duration": "3"}}
            with mock.patch.object(production_smoke, "_run", return_value=SimpleNamespace(stdout=json.dumps(probe))):
                report = production_smoke._verify_output(root / "ffprobe", root, 3, expect_dubbing=True,
                                                        expect_voice_id="vi-truc-ly-vieneu3-v1")
                self.assertEqual(report["streaming_mix"]["producer_version"], "2.0.1")
                self.assertEqual(report["streaming_mix"]["tts_checkpoint_records_verified"], 1)
                current_tts = json.loads(tts_path.read_text())
                historical_tts = json.loads(tts_path.read_text())
                historical_tts["provenance"]["producer_version"] = "3.1.0"
                tts_path.write_text(json.dumps(historical_tts))
                with self.assertRaisesRegex(production_smoke.SmokeError, "selected native producer"):
                    production_smoke._verify_output(root / "ffprobe", root, 3, expect_dubbing=True)
                tts_path.write_text(json.dumps(current_tts))
                saved_record = record.read_bytes()
                record.write_bytes(b"{")
                with self.assertRaisesRegex(production_smoke.SmokeError, "per-cue checkpoint"):
                    production_smoke._verify_output(root / "ffprobe", root, 3, expect_dubbing=True)
                record.write_bytes(saved_record)
                mix["provenance"] = {**provenance, "producer_version": "1.0.0"}
                mix_path.write_text(json.dumps(mix))
                with self.assertRaisesRegex(production_smoke.SmokeError, "pinned streaming"):
                    production_smoke._verify_output(root / "ffprobe", root, 3, expect_dubbing=True)
                mix["provenance"] = provenance
                mix_path.write_text(json.dumps(mix))
                with Path(artifacts["final_mix"]["path"]).open("r+b") as handle:
                    handle.seek(-2, 2)
                    handle.write(b"\x02\x01")
                with self.assertRaisesRegex(production_smoke.SmokeError, "hash differs"):
                    production_smoke._verify_output(root / "ffprobe", root, 3, expect_dubbing=True)

    def test_voice_version_evidence_rejects_export_mutation_and_reused_audio(self) -> None:
        for defect in (None, "original_export_mutated", "identical_dialogue"):
            with self.subTest(defect=defect), TemporaryDirectory() as directory:
                root = Path(directory)
                original = root / "original"
                (original / "editable").mkdir(parents=True)
                (original / "editable/dialogue_stem.wav").write_bytes(b"original voice")
                (original / "captions_vi.srt").write_bytes(b"original subtitles")
                (root / "control/jobs").mkdir(parents=True)
                (root / "control/jobs/smoke-good.json").write_bytes(b"original status")
                with closing(sqlite3.connect(root / "control/jobs.sqlite3")) as connection, connection:
                    connection.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, status TEXT)")
                    connection.execute("INSERT INTO jobs VALUES('smoke-good','succeeded')")

                def complete_variant(supervisor, app, data, source, output, job_id, **options):
                    self.assertEqual(options["tts_voice_id"], "vi-thai-son-vieneu3-v1")
                    (output / "editable").mkdir(parents=True)
                    (output / "editable/dialogue_stem.wav").write_bytes(
                        b"original voice" if defect == "identical_dialogue" else b"different voice")
                    if defect == "original_export_mutated":
                        (original / "captions_vi.srt").write_bytes(b"overwritten subtitles")
                    with closing(sqlite3.connect(data / "control/jobs.sqlite3")) as connection, connection:
                        connection.execute("INSERT INTO jobs VALUES(?, 'succeeded')", (job_id,))
                    return {"job_id": job_id, "status": {"state": "COMPLETED"}}, False, ""

                with mock.patch.object(production_smoke, "_run_supervisor", side_effect=complete_variant), \
                        mock.patch.object(production_smoke, "_verify_output", return_value={"fixture_only": True}):
                    if defect is None:
                        report = production_smoke._verify_voice_version(root / "supervisor", root, root, root / "source.mp4",
                            original, root / "ffprobe", voice_id="vi-truc-ly-vieneu3-v1", timeout=10)
                        self.assertEqual(report["original_exports_and_status"], "preserved")
                        self.assertNotEqual(report["original_dialogue_sha256"], report["new_dialogue_sha256"])
                    else:
                        message = "original export" if defect == "original_export_mutated" else "identical dialogue"
                        with self.assertRaisesRegex(production_smoke.SmokeError, message):
                            production_smoke._verify_voice_version(root / "supervisor", root, root, root / "source.mp4",
                                original, root / "ffprobe", voice_id="vi-truc-ly-vieneu3-v1", timeout=10)


if __name__ == "__main__":
    unittest.main()

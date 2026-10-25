from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from engine.dubflow.media import MediaAdapterError, parse_ffprobe_json
from engine.dubflow.separation import spleeter
from engine.dubflow.separation.production import SeparationError
from engine.dubflow.tts import approved_default_voice
from engine.dubflow.worker import production_job as worker
from engine.dubflow.worker.b2_audio import _restore_source_windows, B2AudioError

ROOT = Path(__file__).resolve().parents[3]
CUES = (worker.TextCue("a", 0, 1000, "你好"),)
METRICS = {"backend": spleeter.BACKEND, "seconds": .1, "source_rms_db": -20.,
           "background_rms_db": -25., "removed_db": -22., "peak": .1}


def write_wav(path, *, rate=48000, channels=2, value=500):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(struct.pack("<" + "h" * channels, *([value] * channels)) * rate * 3)
    return path


class SeparationWorkerTests(unittest.TestCase):
    def test_source_restore_streams_unioned_windows_with_short_fades(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, background, output = root / "source.wav", root / "background.wav", root / "bed.wav"
            write_wav(source, rate=22050, value=1200)
            write_wav(background, rate=22050, value=-400)
            before = background.read_bytes()
            _restore_source_windows(background, source, output, [(500, 1100), (900, 1500), (2000, 2001)], block_frames=257)
            self.assertEqual(background.read_bytes(), before)
            with wave.open(str(output), "rb") as reader:
                self.assertEqual((reader.getnframes(), reader.getnchannels(), reader.getframerate()), (66150, 2, 22050))
                values = tuple(struct.iter_unpack("<hh", reader.readframes(reader.getnframes())))
            self.assertTrue(all(frame == (-400, -400) for frame in values[:11025]))
            self.assertTrue(all(frame == (1200, 1200) for frame in values[11466:32634]))
            self.assertTrue(all(frame == (-400, -400) for frame in values[33075:44100]))
            self.assertTrue(all(frame == (-400, -400) for frame in values[44123:]))
            self.assertEqual(values[11025], (-400, -400))
            self.assertEqual(values[33074], (-400, -400))
            self.assertTrue(-400 < values[11245][0] < 1200)
            self.assertTrue(-400 < values[32854][0] < 1200)
            self.assertEqual(values[22050], (1200, 1200))  # no seam at overlapped slot edges
            self.assertTrue(any(frame == (1200, 1200) for frame in values[44100:44123]))
            original_output = output.read_bytes()
            write_wav(background, rate=16000, value=-400)
            with self.assertRaises(B2AudioError):
                _restore_source_windows(background, source, output, [(500, 1500)])
            self.assertEqual(output.read_bytes(), original_output)
    @contextmanager
    def fixture(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"controlled media seam")
            (root / "ffmpeg.exe").write_bytes(b"controlled media executable")
            config = worker.WorkerConfig("separation", "local-file", source, root / "output", ROOT, root / "models",
                root, root / "ffmpeg.exe", root / "ffprobe.exe", source_language="zh", enable_dubbing=True,
                separate_background=True)
            probe = parse_ffprobe_json(json.dumps({"streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "time_base": "1/1000", "start_pts": 0, "duration_ts": 3000},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "time_base": "1/48000", "start_pts": 0,
                 "duration_ts": 144000, "sample_rate": "48000", "channels": 2}]}), source_path=source)
            media = Mock(extract_audio=Mock(side_effect=lambda source, target, **kw:
                write_wav(target, rate=kw["sample_rate"], channels=kw["channels"])))
            media.render.side_effect = lambda source, target, **kw: target.write_bytes(b"rendered video seam")
            yield root, config, probe, media

    def infer(self, source, background, **kwargs):
        write_wav(background, value=200)
        return dict(METRICS)

    def stage(self, config, probe, media, work, checkpoint, warnings):
        return worker._separate_background_stage(config, "source-hash", probe, media, work, checkpoint,
                                                work / "checkpoint.json", Mock(), warnings)

    def test_stage_reuses_hash_verified_checkpoint_and_recipe_changes_invalidate(self):
        with self.fixture() as (root, config, probe, media), patch.object(worker, "ensure_model_profile") as download, \
             patch.object(spleeter, "separate_background", side_effect=self.infer) as infer:
            work, checkpoint, warnings = root / "work", {"stages": {}}, []
            path, summary = self.stage(config, probe, media, work, checkpoint, warnings)
            self.assertEqual(path, work / "separation/background.wav")
            self.assertEqual(summary["mode"], "spleeter-2stems")
            self.assertEqual(summary["backend"], spleeter.BACKEND)
            self.assertEqual(media.extract_audio.call_args.kwargs,
                dict(sample_rate=48000, channels=2, video_stream=probe.video, overwrite=True))
            self.assertEqual(infer.call_args.kwargs["model_dir"], config.model_root / "separation/spleeter-2stems-v1")
            self.assertEqual(self.stage(config, probe, media, work, checkpoint, warnings), (path, summary))
            self.assertEqual(infer.call_count, 1)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(media.extract_audio.call_count, 1)
            identity = checkpoint["stages"]["separation"]["input_hash"]
            actual_hash = worker._sha256
            for changed in (config.app_root / worker.SEPARATION_PROFILE_PATH, Path(spleeter.__file__), config.ffmpeg_path):
                with patch.object(worker, "_sha256", side_effect=lambda p: "sha256:" + "f" * 64 if Path(p) == changed else actual_hash(p)):
                    self.stage(config, probe, media, work, checkpoint, warnings)
                    self.assertNotEqual(checkpoint["stages"]["separation"]["input_hash"], identity)
            path.write_bytes(b"corrupt")
            self.stage(config, probe, media, work, checkpoint, warnings)
            self.assertEqual(infer.call_count, 5)
            self.assertEqual(warnings, [])

    def test_optional_download_decode_model_gate_and_checkpoint_errors_fall_back(self):
        for failing in ("download", "decode", "model", "gate", "checkpoint"):
            with self.subTest(failing=failing), self.fixture() as (root, config, probe, media), \
                 patch.object(worker, "ensure_model_profile") as download, \
                 patch.object(spleeter, "separate_background", side_effect=self.infer) as infer:
                work, warnings = root / "work", []
                work.mkdir()
                if failing == "download":
                    download.side_effect = OSError("offline")
                elif failing == "decode":
                    media.extract_audio.side_effect = RuntimeError("decoder failed")
                elif failing == "model":
                    infer.side_effect = RuntimeError("native model failed")
                elif failing == "gate":
                    infer.side_effect = SeparationError("SEPARATION_REJECTED", "clips")
                else:
                    (work / "checkpoint.json").mkdir()
                self.assertEqual(self.stage(config, probe, media, work, {"stages": {}}, warnings), (None, {"mode": "original"}))
                self.assertEqual(len(warnings), 1)
                self.assertTrue(warnings[0].startswith("SEPARATION_SKIPPED:"))
                if failing == "gate":
                    self.assertEqual(warnings[0], "SEPARATION_SKIPPED: SEPARATION_REJECTED: clips")

    def fake_b2(self, **kwargs):
        root = kwargs["work_dir"]
        root.mkdir(parents=True)
        original = write_wav(root / "source_audio.wav")
        background = None
        if kwargs.get("background_path") is not None:
            background = root / "background_stem.wav"
            background.write_bytes(kwargs["background_path"].read_bytes())
        dialogue, final = write_wav(root / "dialogue.wav"), write_wav(root / "final.wav")
        def artifact(path):
            return SimpleNamespace(path=str(path), content_hash=worker._sha256(path))
        tts_path, mix_path = root / "tts_document.json", root / "mix_document.json"
        tts_path.write_text("{}")
        mix_path.write_text("{}")
        document = SimpleNamespace(provenance=SimpleNamespace(backend_id="hermetic-tts"), failures=(), warnings=())
        mix = SimpleNamespace(provenance=SimpleNamespace(to_dict=lambda: {}), failures=(), warnings=(),
            original_audio=artifact(background or original), dialogue_stem=artifact(dialogue), final_mix=artifact(final))
        return SimpleNamespace(final_mix_path=final, original_audio_path=original, source_audio_path=original,
            background_stem_path=background, dialogue_stem_path=dialogue, tts_document_path=tts_path, mix_document_path=mix_path,
            tts_document=document, mix_document=mix, voice=approved_default_voice(), placement_document_path=None,
            spoken_text_document_path=None)

    @contextmanager
    def pipeline(self, probe, media):
        runtime = SimpleNamespace(provenance=lambda: {"backend": "hermetic-translation"})
        with patch.object(worker, "ensure_model_profile"), \
             patch.object(worker, "MediaProbe") as probing, \
             patch.object(worker, "FfmpegMediaAdapter", return_value=media), \
             patch.object(worker, "_load_sidecar", return_value=CUES), \
             patch.object(worker, "create_translation_runtime", return_value=runtime), \
             patch.object(worker, "_translate_with_argos", return_value=tuple(replace(cue, translated_text="Xin chào") for cue in CUES)) as translate, \
             patch.object(worker, "run_b2_audio", side_effect=self.fake_b2) as dub:
            probing.return_value.probe.return_value = probe
            yield dub, translate

    def test_pipeline_exports_background_original_and_hashes_and_toggle_remixes(self):
        with self.fixture() as (root, config, probe, media), self.pipeline(probe, media) as (dub, translate), \
             patch.object(spleeter, "separate_background", side_effect=self.infer) as infer:
            result = worker.run_local_file(config, Mock())
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            qc = json.loads(Path(result["qc"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["background"], qc["background"])
            self.assertEqual(manifest["background"]["mode"], "spleeter-2stems")
            self.assertEqual(manifest["audio"]["bed"], "spleeter-2stems")
            for name in ("source_audio", "background_stem", "editable_source_audio", "editable_background_stem"):
                item = manifest["artifacts"][name]
                self.assertEqual(item["sha256"], worker._sha256(Path(item["path"])))
            self.assertNotEqual(manifest["artifacts"]["source_audio"]["sha256"], manifest["artifacts"]["background_stem"]["sha256"])
            first_identity = dub.call_args.kwargs["work_dir"].parent.name
            worker.run_local_file(config, Mock())
            self.assertEqual(infer.call_count, 1)
            result = worker.run_local_file(replace(config, separate_background=False), Mock())
            self.assertNotIn("background_path", dub.call_args.kwargs)
            self.assertNotEqual(first_identity, dub.call_args.kwargs["work_dir"].parent.name)
            self.assertEqual(translate.call_count, 1)
            self.assertFalse((config.output_dir / "editable/background_stem.wav").exists())
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["background"], {"mode": "original"})
            self.assertEqual(manifest["audio"]["bed"], "source")

    def test_fit_and_restoration_advisories_reach_job_manifest_and_qc(self):
        advisories = ("TTS_RENDER_WINDOW_WIDENED: a; end 3000", "TTS_FAST_FIT: a; speed 1400")
        restored = "SEPARATION_SOURCE_RESTORED: b"
        with self.fixture() as (root, config, probe, media), self.pipeline(probe, media) as (dub, _), \
             patch.object(spleeter, "separate_background", side_effect=self.infer):
            def warned(**kwargs):
                audio = self.fake_b2(**kwargs)
                audio.tts_document.warnings = advisories
                audio.mix_document.warnings = (restored,)
                audio.spoken_text_document_path = kwargs["work_dir"] / "dubbing_spoken_text.json"
                audio.spoken_text_document_path.write_text("{}")
                return audio
            dub.side_effect = warned
            result = worker.run_local_file(config, Mock())
            for name in ("manifest", "qc"):
                document = json.loads(Path(result[name]).read_text(encoding="utf-8"))
                self.assertTrue(set((*advisories, restored)) <= set(document["warnings"]))

    def test_pipeline_skips_when_disabled_without_dubbing_or_without_audio(self):
        for enabled, dubbing, audio in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(enabled=enabled, dubbing=dubbing, audio=audio), self.fixture() as (root, config, probe, media):
                config = replace(config, separate_background=enabled, enable_dubbing=dubbing)
                if not audio:
                    probe = replace(probe, streams=(probe.video,))
                with self.pipeline(probe, media), patch.object(worker, "_separate_background_stage") as stage:
                    result = worker.run_local_file(config, Mock())
                stage.assert_not_called()
                self.assertEqual(json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))["background"], {"mode": "original"})

    def test_pipeline_failure_keeps_aud0_and_retries_bad_bed_once(self):
        for failure in ("separation", "bed", "vanished", "b2", "render"):
            with self.subTest(failure=failure), self.fixture() as (root, config, probe, media), \
                 self.pipeline(probe, media) as (dub, translate), patch.object(spleeter, "separate_background", side_effect=self.infer) as infer:
                if failure == "separation":
                    infer.side_effect = SeparationError("SEPARATION_REJECTED", "duration mismatch")
                elif failure == "bed":
                    def dub_bad_bed(**kwargs):
                        if kwargs.get("background_path") is not None:
                            raise worker.B2AudioError("SEPARATION_BED_FAILED", "bed read failed")
                        return self.fake_b2(**kwargs)
                    dub.side_effect = dub_bad_bed
                elif failure == "b2":
                    dub.side_effect = worker.B2AudioError("TTS_FAILED", "no voice")
                elif failure == "render":
                    def render(source, target, **kwargs):
                        if kwargs.get("audio_path") is not None:
                            raise MediaAdapterError("AUDIO_RENDER_FAILED", "mux failed")
                        target.write_bytes(b"valid B1 render seam")
                    media.render.side_effect = render
                emitter = Mock()
                if failure == "vanished":
                    emitter.checkpoint.side_effect = lambda stage, *_: (
                        (config.output_dir / ".dubflow-work/separation/background.wav").unlink()
                        if stage == "separation" else None)
                result = worker.run_local_file(config, emitter)
                manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
                self.assertEqual(manifest["background"], {"mode": "original"})
                self.assertEqual(manifest["audio"]["mode"], "original" if failure in ("b2", "render") else "dubbed")
                if failure in ("bed", "separation", "vanished"):
                    self.assertTrue(any(warning.startswith("SEPARATION_SKIPPED:") for warning in manifest["warnings"]))
                    self.assertNotIn("background_path", dub.call_args.kwargs)
                if failure == "bed":
                    self.assertEqual(dub.call_count, 2)
                    self.assertNotEqual(dub.call_args_list[0].kwargs["work_dir"].parent.name,
                                        dub.call_args_list[1].kwargs["work_dir"].parent.name)


if __name__ == "__main__":
    unittest.main()

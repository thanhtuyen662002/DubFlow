"""Issue #320: a dubbed job stores each distinct full-length track once."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from engine.dubflow.media import parse_ffprobe_json
from engine.dubflow.mix import streaming
from engine.dubflow.separation import spleeter
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice
from engine.dubflow.worker import export_publication
from engine.dubflow.worker import production_job as worker

ROOT = Path(__file__).resolve().parents[3]
SECONDS = 60
CUES = tuple(worker.TextCue(f"cue-{index}", index * 10_000, index * 10_000 + 1000, "你好") for index in range(6))
METRICS = {"backend": spleeter.BACKEND, "seconds": .1, "source_rms_db": -20.,
           "background_rms_db": -25., "removed_db": -22., "peak": .1}


class Interrupted(BaseException):
    """A process stop: optional stages must not absorb it as a skip."""


def write_wav(path: Path, *, rate: int, channels: int, value: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(struct.pack("<" + "h" * channels, *([value] * channels)) * rate * SECONDS)
    return path


def tracks(*roots: Path, minimum: int) -> dict[tuple[int, int], int]:
    """Distinct stored full-length audio files, counted once per inode."""
    stored = {}
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".wav", ".pcm"} and path.stat().st_size >= minimum:
                status = path.stat()
                stored[(status.st_dev, status.st_ino)] = status.st_size
    return stored


class DubbedJobStorageTests(unittest.TestCase):
    @contextmanager
    def job(self, *, separate=True):
        rate = worker._b2_sample_rate(ROOT)
        with TemporaryDirectory(prefix="dubflow-disk-") as directory, ExitStack() as stack:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"user media that must never change")
            (root / "ffmpeg.exe").write_bytes(b"controlled media executable")
            config = worker.WorkerConfig("disk", "local-file", source, root / "output", ROOT, root / "models", root,
                root / "ffmpeg.exe", root / "ffprobe.exe", source_language="zh", enable_dubbing=True,
                separate_background=separate)
            probe = parse_ffprobe_json(json.dumps({"streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264", "time_base": "1/1000", "start_pts": 0,
                 "duration_ts": SECONDS * 1000},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "time_base": "1/48000", "start_pts": 0,
                 "duration_ts": SECONDS * 48000, "sample_rate": "48000", "channels": 2}]}), source_path=source)
            media = Mock(ffmpeg_path=root / "ffmpeg.exe")
            media.extract_audio.side_effect = lambda _source, target, **kw: write_wav(
                target, rate=kw.get("sample_rate", 16_000), channels=kw.get("channels", 1), value=900)
            media.render.side_effect = lambda _source, target, **kw: target.write_bytes(b"rendered video seam")

            def separate_background(source_wav, background_wav, **_kwargs):
                with wave.open(str(source_wav), "rb") as reader:
                    rate_, channels = reader.getframerate(), reader.getnchannels()
                chunks = background_wav.parent / ".spleeter-chunks" / "identity"
                chunks.mkdir(parents=True, exist_ok=True)
                (chunks / "batch-0.npz").write_bytes(b"\0" * 4096)
                write_wav(background_wav, rate=rate_, channels=channels, value=300)
                return dict(METRICS)

            runtime = SimpleNamespace(provenance=lambda: {"backend": "hermetic-translation"})
            stack.enter_context(patch.object(worker, "ensure_model_profile"))
            probing = stack.enter_context(patch.object(worker, "MediaProbe"))
            probing.return_value.probe.return_value = probe
            stack.enter_context(patch.object(worker, "FfmpegMediaAdapter", return_value=media))
            stack.enter_context(patch.object(worker, "_load_sidecar", return_value=CUES))
            stack.enter_context(patch.object(worker, "create_translation_runtime", return_value=runtime))
            stack.enter_context(patch.object(worker, "_translate_with_argos",
                return_value=tuple(replace(cue, translated_text="Xin chào") for cue in CUES)))
            stack.enter_context(patch("engine.dubflow.worker.b2_audio.vieneu.load_vieneu_voice",
                return_value=(SimpleNamespace(sample_rate=rate, channels=1), approved_default_voice())))
            stack.enter_context(patch("engine.dubflow.worker.b2_audio.vieneu.VieNeuVietnameseTtsEngine",
                side_effect=lambda *a, **kw: DeterministicFixtureEngine()))
            separation = stack.enter_context(patch.object(spleeter, "separate_background", side_effect=separate_background))
            computes = stack.enter_context(patch.object(streaming.StreamingAudioMixer, "_compute",
                autospec=True, side_effect=streaming.StreamingAudioMixer._compute))
            yield SimpleNamespace(root=root, config=config, media=media, separation=separation, computes=computes,
                                  track=SECONDS * rate * 4, source=source)

    def assert_published(self, job):
        output = job.config.output_dir
        manifest = json.loads((output / "job_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["audio"]["mode"], "dubbed")
        for item in manifest["artifacts"].values():
            self.assertEqual(item["sha256"], worker._sha256(Path(item["path"])), item["path"])
        mix = json.loads(Path(manifest["audio"]["mix_document"]).read_text(encoding="utf-8"))
        for key, name in (("original_audio", None), ("dialogue_stem", "dialogue_stem.wav"), ("final_mix", "final_mix.wav")):
            self.assertEqual(worker._sha256(Path(mix[key]["path"])), mix[key]["content_hash"])
            if name is not None:
                self.assertEqual(worker._sha256(output / "editable" / name), mix[key]["content_hash"])
        self.assertEqual(job.source.read_bytes(), b"user media that must never change")
        return manifest

    def test_finished_job_keeps_one_copy_of_each_distinct_track(self):
        for separate in (True, False):
            with self.subTest(separate=separate), self.job(separate=separate) as job:
                worker.run_local_file(job.config, Mock())
                manifest = self.assert_published(job)
                output, work = job.config.output_dir, job.config.output_dir / ".dubflow-work"
                stored = tracks(work, output / "editable", minimum=job.track // 2)
                # Source, dialogue and final mix; a separated bed adds the background.
                self.assertEqual(len(stored), 4 if separate else 3, stored)
                self.assertLessEqual(sum(stored.values()), 4 * (job.track + 64))
                self.assertEqual(list(work.rglob("*.pcm")), [])
                self.assertEqual(list(work.rglob("mix_bed.wav")), [])
                self.assertFalse((work / "separation/source_audio.wav").exists())
                self.assertFalse((work / "separation/.spleeter-chunks").exists())
                for name in ("source_audio.wav", "dialogue_stem.wav", "final_mix.wav"):
                    self.assertGreaterEqual((output / "editable" / name).stat().st_nlink, 2)
                self.assertNotIn("EDITABLE_LINK_FALLBACK", " ".join(manifest["warnings"]))

    def test_link_fallback_copies_and_is_recorded(self):
        with self.job(separate=False) as job, patch.object(worker.os, "link", side_effect=OSError("exFAT")):
            worker.run_local_file(job.config, Mock())
            manifest = self.assert_published(job)
            qc = json.loads((job.config.output_dir / "qc_report.json").read_text(encoding="utf-8"))
            for document in (manifest, qc):
                self.assertTrue(any(warning.startswith("EDITABLE_LINK_FALLBACK:") for warning in document["warnings"]))
            self.assertEqual((job.config.output_dir / "editable/final_mix.wav").stat().st_nlink, 1)

    def test_interrupted_job_resumes_from_checkpoints_after_cleanup(self):
        for stage in ("separation", "b2-audio", "render-dubbed"):
            with self.subTest(stage=stage), self.job() as job:
                emitter = Mock()
                emitter.checkpoint.side_effect = lambda name, *_: (_ for _ in ()).throw(Interrupted(name)) if name == stage else None
                with self.assertRaises(Interrupted):
                    worker.run_local_file(job.config, emitter)
                worker.run_local_file(job.config, Mock())
                self.assert_published(job)
                self.assertEqual(job.separation.call_count, 1)
                # A committed mix is reused, not recomputed, after its spools are gone.
                self.assertEqual(job.computes.call_count, 1)

    def test_failed_publication_never_reuses_a_linked_generation(self):
        with self.job() as job:
            real = export_publication.publish
            with patch.object(export_publication, "publish", side_effect=OSError("disk removed")):
                with self.assertRaises(worker.ProductionJobError):
                    worker.run_local_file(job.config, Mock())
            with patch.object(export_publication, "publish", side_effect=real):
                worker.run_local_file(job.config, Mock())
            self.assert_published(job)
            self.assertEqual(job.computes.call_count, 2)
            generations = [path for path in (job.config.output_dir / ".dubflow-work/b2-audio").glob("*/*") if path.is_dir()]
            self.assertEqual(len(generations), 1)

    def test_rerun_retires_earlier_generations(self):
        with self.job() as job:
            for _ in range(3):
                worker.run_local_file(job.config, Mock())  # Published audio is immutable: each run is a new generation.
            self.assertEqual(job.computes.call_count, 3)
            self.assert_published(job)
            generations = [path for path in (job.config.output_dir / ".dubflow-work/b2-audio").glob("*/*") if path.is_dir()]
            self.assertEqual(len(generations), 1)
            output = job.config.output_dir
            self.assertEqual(len(tracks(output / ".dubflow-work", output / "editable", minimum=job.track // 2)), 4)


class StorageAdmissionTests(unittest.TestCase):
    def config(self, root: Path, **changes):
        source = root / "source.mp4"
        source.write_bytes(b"\0" * 1000)
        return replace(worker.WorkerConfig("disk", "local-file", source, root / "output", ROOT, root / "models", root,
            root / "ffmpeg.exe", root / "ffprobe.exe", source_language="zh", enable_dubbing=True,
            separate_background=True), **changes)

    def required(self, config, milliseconds, tracks):
        return (milliseconds * 32 + 2 * config.source_path.stat().st_size + worker.STORAGE_RESERVE_BYTES
                + tracks * (milliseconds * worker._b2_sample_rate(ROOT) // 1000 + 1) * 4)

    def test_estimate_covers_the_peak_and_credits_a_resumed_work_folder(self):
        probe = SimpleNamespace(duration_ticks=10_320_000, has_audio=True)  # The 2 h 52 min #291 source.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.config(root)
            work = config.output_dir / ".dubflow-work"
            work.mkdir(parents=True)
            for separate, dubbing, tracks_ in ((True, True, 10), (False, True, 7), (True, False, 0)):
                with self.subTest(separate=separate, dubbing=dubbing):
                    changed = replace(config, separate_background=separate, enable_dubbing=dubbing)
                    required = self.required(changed, probe.duration_ticks, tracks_)
                    with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=required)):
                        worker._storage_admission(changed, probe, work)
                    with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=required - 1)):
                        with self.assertRaises(worker.ProductionJobError) as raised:
                            worker._storage_admission(changed, probe, work)
                    self.assertEqual(raised.exception.code, "STORAGE_INSUFFICIENT")
                    self.assertIn("giải phóng dung lượng", raised.exception.condition)
            (work / "held.bin").write_bytes(b"\0" * 4096)
            required = self.required(config, probe.duration_ticks, 10)
            with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=required - 4096)):
                worker._storage_admission(config, probe, work)

    def test_low_space_stops_the_job_before_large_allocations_and_keeps_existing_work(self):
        with DubbedJobStorageTests().job() as job:
            output = job.config.output_dir
            previous = output / "final_vi.mp4"
            output.mkdir(parents=True)
            previous.write_bytes(b"previously published video")
            checkpoint = output / ".dubflow-work/asr-chunks/chunk-0.json"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_bytes(b"committed ASR checkpoint")
            with patch.object(worker.shutil, "disk_usage", return_value=SimpleNamespace(free=10**6)), \
                 patch.object(worker, "run_b2_audio") as dub:
                with self.assertRaises(worker.ProductionJobError) as raised:
                    worker.run_local_file(job.config, Mock())
            self.assertEqual(raised.exception.code, "STORAGE_INSUFFICIENT")
            self.assertFalse(raised.exception.retryable)
            job.media.extract_audio.assert_not_called()
            job.separation.assert_not_called()
            dub.assert_not_called()
            self.assertEqual(previous.read_bytes(), b"previously published video")
            self.assertEqual(checkpoint.read_bytes(), b"committed ASR checkpoint")
            self.assertEqual(job.source.read_bytes(), b"user media that must never change")
            # The next item in the queue has its own output folder and room to run.
            other = replace(job.config, output_dir=job.root / "other-output")
            worker.run_local_file(other, Mock())
            self.assertTrue((other.output_dir / "final_vi.mp4").is_file())


if __name__ == "__main__":
    unittest.main()

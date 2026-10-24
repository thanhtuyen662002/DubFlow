from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
import json
import math
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.media import parse_ffprobe_json
from engine.dubflow.tts import DeterministicFixtureEngine, approved_default_voice, parse_tts_json
from engine.dubflow.worker import b2_audio, production_job as worker
from engine.dubflow.worker.speaker_audio import run_speaker_audio
from engine.dubflow.worker.speaker_casting import speaker_plan, speaker_recipe_identity


ROOT = Path(__file__).resolve().parents[3]
VOICES = ("vi-truc-ly-vieneu3-v1", "vi-thuy-dung-vieneu3-v1")
CUES = (worker.TextCue("a", 0, 600, "Hello", "Xin chao"),
    worker.TextCue("b", 1050, 1650, "World", "The gioi"),
    worker.TextCue("c", 2100, 2700, "Bye", "Tam biet"))


class FixtureMedia:
    ffmpeg_path = Path("unused-fixture-ffmpeg")

    def __init__(self):
        self.analysis_extractions = 0

    def extract_audio(self, source, output, *, channels=1, sample_rate=16000, **options):
        self.analysis_extractions += channels == 1
        samples = []
        for milliseconds, frequency in ((700, 180), (350, 0), (700, 420), (350, 0), (700, 180), (200, 0)):
            for index in range(sample_rate * milliseconds // 1000):
                sample = int(9000 * math.sin(2 * math.pi * frequency * index / sample_rate)) if frequency else 0
                samples.extend([sample] * channels)
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        return output

    def render(self, source, output, **options):
        output.write_bytes(b"fixture-render")
        return output


class RecordingEngine(DeterministicFixtureEngine):
    def __init__(self, calls, closes):
        super().__init__()
        self.requests = calls
        self.closes = closes

    def synthesize(self, request):
        self.requests.append((request.segment.segment_id, request.voice.voice_id, request.segment.render_window_end.ticks))
        return super().synthesize(request)

    def close(self):
        self.closes.append(True)


class SpeakerAudioIntegrationTests(unittest.TestCase):
    def fixture(self, root, voices=VOICES):
        source = root / "source.mp4"
        source.write_bytes(b"fixture-source")
        probe = parse_ffprobe_json(json.dumps({"streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "time_base": "1/1000", "start_pts": 0, "duration_ts": 3000},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "time_base": "1/16000", "start_pts": 0, "duration_ts": 48000}
        ], "format": {"duration": "3.0"}}), source_path=source)
        args = {"job_id": "speaker-job", "stage_id": "local-file", "source_path": str(source),
            "output_dir": str(root / "output"), "app_root": str(ROOT), "model_root": str(root / "models"),
            "media_runtime_root": str(root), "ffmpeg_path": str(root / "ffmpeg"), "ffprobe_path": str(root / "ffprobe"),
            "source_language": "en", "enable_dubbing": True, "tts_voice_id": VOICES[0]}
        if voices is not None:
            args["speaker_voices"] = list(voices)
        return worker.WorkerConfig.from_args(args), probe

    def patches(self, stack, probe, media, calls, closes):
        def load(*args, voice_id=None, **kwargs):
            return SimpleNamespace(sample_rate=16000, channels=1), replace(approved_default_voice(), voice_id=voice_id or VOICES[0])

        stack.enter_context(patch.object(worker, "ensure_model_profile"))
        stack.enter_context(patch.object(worker, "MediaProbe", return_value=SimpleNamespace(probe=lambda _path: probe)))
        stack.enter_context(patch.object(worker, "FfmpegMediaAdapter", return_value=media))
        stack.enter_context(patch.object(worker, "_load_sidecar", return_value=CUES))
        stack.enter_context(patch.object(worker, "ArgosRuntime", return_value=SimpleNamespace(provenance=lambda: {"backend": "fixture"})))
        stack.enter_context(patch.object(worker, "_translate_with_argos", return_value=CUES))
        stack.enter_context(patch.object(b2_audio.vieneu, "load_vieneu_voice", side_effect=load))
        stack.enter_context(patch.object(b2_audio.vieneu, "VieNeuVietnameseTtsEngine", side_effect=lambda *args, **kwargs: RecordingEngine(calls, closes)))

    def test_worker_routes_two_speakers_and_records_manifest_without_changing_source_slots(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config, probe = self.fixture(root)
            calls, closes, media = [], [], FixtureMedia()
            self.patches(stack, probe, media, calls, closes)
            result = worker.run_local_file(config, SimpleNamespace(progress=lambda *a, **k: None, checkpoint=lambda *a: None))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["mode"], "dubbed", manifest["warnings"])
            plan = manifest["audio"]["speaker_dubbing"]
            self.assertEqual(plan["cue_to_voice"], {"a": VOICES[0], "b": VOICES[1], "c": VOICES[0]})
            self.assertEqual(set(plan["speaker_to_voice"].values()), set(VOICES))
            self.assertEqual(plan["diarization"]["audio"]["sample_rate"], 16000)
            self.assertEqual(plan["diarization"]["backend"]["model_id"], "dubflow-cpu-energy-diarizer")
            self.assertEqual(manifest["dubbing"]["speaker_voices"], list(VOICES))
            self.assertEqual({cue: voice for cue, voice, _window in calls}, plan["cue_to_voice"])
            self.assertEqual(dict((cue, end) for cue, _voice, end in calls)["a"], 930)
            self.assertEqual(media.analysis_extractions, 1)  # explicit-language sidecar still needs diarization audio
            self.assertEqual(len(closes), 2)
            self.assertEqual(manifest["cues"], [cue.to_dict() for cue in CUES])
            speech = parse_tts_json(Path(manifest["audio"]["tts_document"]).read_bytes())
            self.assertEqual(speech["provenance"]["voice_id"], "composite")
            self.assertEqual(len(plan["voice_documents"]), 2)
            self.assertTrue(all(Path(item["tts_document"]).is_file() for item in plan["voice_documents"]))
            calls.clear()
            with patch("engine.dubflow.worker.speaker_casting.diarize_wav", side_effect=AssertionError("completed plan must resume")) as diarizer:
                worker.run_local_file(config, SimpleNamespace(progress=lambda *a, **k: None, checkpoint=lambda *a: None))
                diarizer.assert_not_called()
            self.assertFalse(calls)  # a fresh published generation can reuse voice-bound speech

    def test_worker_diarization_failure_warns_and_still_dubs_with_primary(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config, probe = self.fixture(root)
            calls, closes, media = [], [], FixtureMedia()
            self.patches(stack, probe, media, calls, closes)
            stack.enter_context(patch("engine.dubflow.worker.speaker_casting.diarize_wav", side_effect=RuntimeError("injected failure")))
            result = worker.run_local_file(config, SimpleNamespace(progress=lambda *a, **k: None, checkpoint=lambda *a: None))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["mode"], "dubbed")
            self.assertEqual({voice for _cue, voice, _window in calls}, {VOICES[0]})
            self.assertEqual(manifest["audio"]["speaker_dubbing"]["fallback_reason"], "DIARIZATION_FAILED")
            self.assertTrue(any("SPEAKER_DIARIZATION_FALLBACK" in warning for warning in manifest["warnings"]))

    def test_absent_setting_keeps_legacy_generation_and_cue_identities(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config, probe = self.fixture(root, None)
            calls, closes, media = [], [], FixtureMedia()
            self.patches(stack, probe, media, calls, closes)
            backend = stack.enter_context(patch("engine.dubflow.worker.speaker_casting.diarize_wav", side_effect=AssertionError("disabled")))
            result = worker.run_local_file(config, SimpleNamespace(progress=lambda *a, **k: None, checkpoint=lambda *a: None))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertNotIn("speaker_voices", manifest["dubbing"])
            self.assertNotIn("speaker_dubbing", manifest["audio"])
            backend.assert_not_called()
            self.assertEqual(media.analysis_extractions, 0)
            work = config.output_dir / ".dubflow-work"
            checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
            translation = json.loads((work / "translation.json").read_text(encoding="utf-8"))["input_hash"]
            profile = ROOT / "models/manifests/production-cpu-v1.json"
            expected = sha256((translation + worker._audio_decode_identity(worker._sha256(config.source_path), probe)
                + worker._sha256(profile) + worker._sha256(Path(b2_audio.__file__))
                + b2_audio.tts_recipe_identity(ROOT, profile) + b2_audio.mix_recipe_identity() + json.dumps(config.tts_voice_id)).encode()).hexdigest()
            self.assertEqual(checkpoint["stages"]["b2-generation"]["input_hash"], expected)
            original = parse_tts_json(Path(manifest["audio"]["tts_document"]).read_bytes())
            legacy = b2_audio.run_b2_audio(media=media, source_path=config.source_path, source_probe=probe,
                translated_cues=CUES, source_language="en", app_root=ROOT, profile_path=profile,
                work_dir=Path(manifest["audio"]["tts_document"]).parent, tts_voice_id=config.tts_voice_id)
            self.assertEqual([item["artifact_hash"] for item in original["artifacts"]], [item.artifact_hash for item in legacy.tts_document.artifacts])

    def test_multivoice_resume_reuses_checkpoints_and_changed_assignment_misses_speech_cache(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config, probe = self.fixture(root)
            calls, closes, media = [], [], FixtureMedia()
            self.patches(stack, probe, media, calls, closes)
            analysis = root / "analysis.wav"
            media.extract_audio(config.source_path, analysis)
            plan = speaker_plan(analysis, config.job_id, CUES, VOICES, VOICES[0])
            plan["producer_identity"] = speaker_recipe_identity(ROOT, VOICES)
            options = dict(media=media, source_path=config.source_path, source_probe=probe, translated_cues=CUES,
                source_language="en", app_root=ROOT, profile_path=ROOT / "models/manifests/production-cpu-v1.json",
                model_root=config.model_root, synthesis_cache_dir=root / "speech-cache")
            first = run_speaker_audio(**options, work_dir=root / "first", speaker_plan=dict(plan))
            self.assertEqual(len(calls), 3)
            hashes = [item.artifact_hash for item in first.tts_document.artifacts]
            calls.clear()
            resumed = run_speaker_audio(**options, work_dir=root / "first", speaker_plan=dict(plan))
            self.assertFalse(calls)
            self.assertEqual([item.artifact_hash for item in resumed.tts_document.artifacts], hashes)
            changed = dict(plan, cue_to_voice={cue.cue_id: VOICES[1] if plan["cue_to_voice"][cue.cue_id] == VOICES[0] else VOICES[0] for cue in CUES})
            second = run_speaker_audio(**options, work_dir=root / "second", speaker_plan=changed)
            self.assertEqual(len(calls), 3)
            self.assertNotEqual([item.artifact_hash for item in second.tts_document.artifacts], hashes)

    def test_refused_voice_group_keeps_other_speakers_usable(self):
        from engine.dubflow.tts import TtsError

        class RefusedEngine(RecordingEngine):
            def synthesize(self, request):
                if request.voice.voice_id == VOICES[1]:
                    raise TtsError("TTS_REFUSED", "fixture refusal")
                return super().synthesize(request)

        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config, probe = self.fixture(root)
            calls, closes, media = [], [], FixtureMedia()
            self.patches(stack, probe, media, calls, closes)
            stack.enter_context(patch.object(b2_audio.vieneu, "VieNeuVietnameseTtsEngine", side_effect=lambda *a, **k: RefusedEngine(calls, closes)))
            result = worker.run_local_file(config, SimpleNamespace(progress=lambda *a, **k: None, checkpoint=lambda *a: None))
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["audio"]["mode"], "dubbed", manifest["warnings"])
            self.assertEqual(manifest["audio"]["tts_failures"], 1)
            self.assertEqual({cue for cue, _voice, _window in calls}, {"a", "c"})
            self.assertTrue(any("B2_AUDIO_DEGRADED" in warning for warning in manifest["warnings"]))


if __name__ == "__main__":
    unittest.main()

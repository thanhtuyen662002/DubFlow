from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.dubflow.asr import TimeBase, TimePoint
from engine.dubflow.tts import (DeterministicFixtureEngine, LocalTtsAdapter, TtsConfig,
    TtsError, TtsInput, TtsProvenance, approved_default_voice)
from engine.dubflow.worker.tts_checkpoints import MAX_RECORD_BYTES, TtsCheckpointStore

INPUT = "sha256:" + "1" * 64
IDENTITY = "2" * 64
SEGMENTS = tuple(TtsInput("cue-" + str(index), "source-" + str(index), "Xin chào",
    TimePoint(index * 1200, TimeBase(1, 1000)), TimePoint(index * 1200 + 1000, TimeBase(1, 1000))) for index in range(2))


class FittedFixture(DeterministicFixtureEngine):
    def __init__(self, mode="padded"):
        super().__init__()
        self.mode = mode
        self.cue_calls = []

    def synthesize(self, request):
        self.cue_calls.append(request.segment.segment_id)
        return replace(super().synthesize(request), fit_mode=self.mode,
                       speed_ratio_milli=1200 if self.mode == "speed_adjusted" else 1000)


def adapter(root: Path, engine=None):
    config = TtsConfig(max_attempts=1, max_segments_per_chunk=1)
    voice = approved_default_voice()
    provenance = TtsProvenance("checkpoint-test", "1", "fixture", "stdlib", "timeline-v1",
        config.content_hash(), INPUT, voice.model_id, voice.model_version, voice.model_hash,
        voice.content_hash(), voice.voice_id, voice.voice_version, config.requested_profile, "fixture", config.resource)
    return LocalTtsAdapter(engine or FittedFixture(), config=config, voice=voice,
                           provenance=provenance, output_dir=root)


class TtsCheckpointTests(unittest.TestCase):
    def test_abrupt_process_exit_resumes_committed_fitted_cue_without_rewriting(self):
        for mode in ("padded", "speed_adjusted"):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                script = """import os, runpy, sys
from pathlib import Path
s = runpy.run_path(sys.argv[1], run_name='test_checkpoint_store')
root = Path(sys.argv[2])
store = s['TtsCheckpointStore'](root, identity=s['IDENTITY'])
def committed(value):
    store.commit(value)
    os._exit(42)
s['adapter'](root, s['FittedFixture'](sys.argv[3])).synthesize(s['SEGMENTS'], input_hash=s['INPUT'], on_checkpoint=committed)
"""
                result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).absolute()), str(root), mode],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 42, result.stderr)
                wav = next(root.glob("tts-*.wav"))
                original = (wav.read_bytes(), wav.stat().st_mtime_ns)
                store = TtsCheckpointStore(root, identity=IDENTITY)
                engine = FittedFixture(mode)
                document = adapter(root, engine).synthesize(SEGMENTS, input_hash=INPUT,
                    checkpoints=store.load(SEGMENTS), on_checkpoint=store.commit)
                self.assertEqual(engine.cue_calls, ["cue-1"])
                self.assertEqual(document.chunks[0]["status"], "skipped")
                self.assertEqual(document.artifacts[0].fit_mode, mode)
                self.assertEqual((wav.read_bytes(), wav.stat().st_mtime_ns), original)
                self.assertEqual(len(store.load(SEGMENTS)), 2)

    def test_corrupt_metadata_or_wave_reprocesses_only_affected_cue(self):
        for corruption in ("json", "checksum", "oversized", "wave"):
            with self.subTest(corruption=corruption), TemporaryDirectory() as directory:
                root = Path(directory)
                store = TtsCheckpointStore(root, identity=IDENTITY)
                first = adapter(root).synthesize(SEGMENTS, input_hash=INPUT, on_checkpoint=store.commit)
                record = store._path("cue-0")
                if corruption == "wave":
                    Path(first.artifacts[0].path).write_bytes(b"not a WAV")
                elif corruption == "oversized":
                    record.write_bytes(b" " * (MAX_RECORD_BYTES + 1))
                elif corruption == "json":
                    record.write_bytes(b"{")
                else:
                    value = json.loads(record.read_text(encoding="utf-8"))
                    value["artifact"]["speed_ratio_milli"] = 1100
                    record.write_text(json.dumps(value), encoding="utf-8")
                store = TtsCheckpointStore(root, identity=IDENTITY)
                engine = FittedFixture()
                document = adapter(root, engine).synthesize(SEGMENTS, input_hash=INPUT,
                    checkpoints=store.load(SEGMENTS), on_checkpoint=store.commit)
                self.assertEqual(engine.cue_calls, ["cue-0"])
                self.assertEqual(document.artifacts[1], first.artifacts[1])
                if corruption != "wave":
                    self.assertEqual(len(store.warnings), 1)

    def test_recipe_change_and_foreign_path_are_never_reused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = TtsCheckpointStore(root, identity=IDENTITY)
            adapter(root).synthesize(SEGMENTS, input_hash=INPUT, on_checkpoint=store.commit)
            changed = TtsCheckpointStore(root, identity="3" * 64)
            self.assertEqual(changed.load(SEGMENTS), {})
            record = store._path("cue-0")
            value = json.loads(record.read_text(encoding="utf-8"))
            value["artifact"]["path"] = str(root.parent / "outside.wav")
            value["artifact_record_hash"] = sha256(json.dumps(value["artifact"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            record.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(set(store.load(SEGMENTS)), {"cue-1"})

    def test_checkpoint_write_failure_stops_without_unchanged_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = TtsCheckpointStore(root, identity=IDENTITY)
            engine = FittedFixture()
            with patch.object(store, "commit", side_effect=OSError("disk full")), self.assertRaises(TtsError) as failed:
                adapter(root, engine).synthesize(SEGMENTS, input_hash=INPUT, on_checkpoint=store.commit)
            self.assertEqual(failed.exception.code, "TTS_CHECKPOINT_WRITE_FAILED")
            self.assertFalse(failed.exception.retryable)
            self.assertEqual(engine.cue_calls, ["cue-0"])


if __name__ == "__main__":
    unittest.main()

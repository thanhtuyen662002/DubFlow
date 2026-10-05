"""Editable audio copies must be bounded and preserve completed artifacts on failure."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.dubflow.worker.production_job import ProductionJobError, _atomic_copy


class GuardedReader:
    def __init__(self, handle, requests, fail_after=None):
        self.handle = handle
        self.requests = requests
        self.fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()

    def read(self, size=-1):
        if not 0 < size <= 4 * 1024 * 1024:
            raise AssertionError("artifact input requires bounded reads")
        self.requests.append(size)
        if self.fail_after is not None and len(self.requests) > self.fail_after:
            raise OSError("simulated interrupted source read")
        return self.handle.read(size)


class InterruptedWriter:
    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()

    def write(self, data):
        self.handle.write(data[:max(1, len(data) // 2)])
        self.handle.flush()
        raise OSError("simulated interrupted artifact write")


class EditableAudioCopyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.input_dir = root / "đầu vào"
        self.output_dir = root / "bản xuất"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        self.source = self.input_dir / "source stereo.wav"
        self.target = self.output_dir / "final mix.wav"
        with wave.open(str(self.source), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            block = b"\x00\x10\x00\xf0" * 8192
            for _ in range(256):
                handle.writeframesraw(block)
        with wave.open(str(self.target), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(b"\x00\x00\x00\x00" * 80)
        self.previous = self.target.read_bytes()

    @staticmethod
    def digest(path):
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()

    def assert_previous_artifact_preserved(self):
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertEqual({path.name for path in self.output_dir.iterdir()}, {self.target.name})

    def test_large_stereo_wav_uses_bounded_reads_and_retains_exact_bytes(self):
        requests = []
        original_open = Path.open

        def open_guarded(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path == self.source and mode == "rb":
                return GuardedReader(handle, requests)
            return handle

        with patch.object(Path, "open", new=open_guarded):
            _atomic_copy(self.source, self.target)
        self.assertGreater(len(requests), 2)
        self.assertEqual(self.digest(self.target), self.digest(self.source))
        self.assertEqual({path.name for path in self.output_dir.iterdir()}, {self.target.name})
        with wave.open(str(self.target), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 2)
            self.assertEqual(handle.getnframes(), 256 * 8192)

    def test_interrupted_source_read_preserves_previous_artifact(self):
        requests = []
        original_open = Path.open

        def open_guarded(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path == self.source and mode == "rb":
                return GuardedReader(handle, requests, fail_after=1)
            return handle

        with patch.object(Path, "open", new=open_guarded):
            with self.assertRaises(ProductionJobError) as caught:
                _atomic_copy(self.source, self.target)
        self.assertEqual(caught.exception.code, "ARTIFACT_COPY_FAILED")
        self.assert_previous_artifact_preserved()

    def test_interrupted_destination_write_preserves_previous_artifact(self):
        original_open = Path.open

        def open_interrupted(path, mode="r", *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path.parent == self.output_dir and "w" in mode:
                return InterruptedWriter(handle)
            return handle

        with patch.object(Path, "open", new=open_interrupted):
            with self.assertRaises(ProductionJobError) as caught:
                _atomic_copy(self.source, self.target)
        self.assertEqual(caught.exception.code, "ARTIFACT_COPY_FAILED")
        self.assert_previous_artifact_preserved()

    def test_failed_durable_flush_preserves_previous_artifact(self):
        with patch("engine.dubflow.worker.production_job.os.fsync", side_effect=OSError("simulated flush failure")):
            with self.assertRaises(ProductionJobError) as caught:
                _atomic_copy(self.source, self.target)
        self.assertEqual(caught.exception.code, "ARTIFACT_COPY_FAILED")
        self.assert_previous_artifact_preserved()

    def test_failed_atomic_replacement_preserves_previous_artifact(self):
        with patch("engine.dubflow.worker.production_job.os.replace", side_effect=OSError("simulated replacement failure")):
            with self.assertRaises(ProductionJobError) as caught:
                _atomic_copy(self.source, self.target)
        self.assertEqual(caught.exception.code, "ARTIFACT_COPY_FAILED")
        self.assert_previous_artifact_preserved()


if __name__ == "__main__":
    unittest.main()

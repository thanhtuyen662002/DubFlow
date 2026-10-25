"""Generation integrity regressions (synthetic PCM, not speech quality)."""
from __future__ import annotations

from hashlib import sha256
from contextlib import ExitStack
import builtins
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from engine.dubflow.separation.production import CpuAttenuationBackend
from engine.dubflow.separation import load_current, read_result
from engine.dubflow.separation import publication


class IOTrace:
    """Inject at real Python read/write/flush/fsync/replace calls, one at a time."""
    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.calls = []
        self.hit = False
        self.max_read = self.max_write = 0

    def operation(self, kind, name, size=0):
        self.calls.append((kind, str(name)))
        if kind == "read":
            if size < 0:
                raise AssertionError("unbounded read")
            self.max_read = max(self.max_read, size)
        if kind == "write":
            self.max_write = max(self.max_write, size)
        if len(self.calls) == self.fail_at:
            self.hit = True
            raise OSError("injected " + kind)

    def wrap(self, stream):
        trace = self

        class Stream:
            def __getattr__(self, name):
                return getattr(stream, name)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                stream.close()

            def read(self, size=-1):
                trace.operation("read", getattr(stream, "name", "spool"), size)
                return stream.read(size)

            def write(self, data):
                trace.operation("write", getattr(stream, "name", "spool"), len(data))
                return stream.write(data)

            def flush(self):
                trace.operation("flush", getattr(stream, "name", "spool"))
                return stream.flush()

        return Stream()

    def install(self, stack):
        path_open, builtin_open, temporary, sync, replace = Path.open, builtins.open, tempfile.TemporaryFile, os.fsync, os.replace
        stack.enter_context(patch.object(Path, "open", lambda path, *args, **kwargs: self.wrap(path_open(path, *args, **kwargs))))
        stack.enter_context(patch("builtins.open", lambda *args, **kwargs: self.wrap(builtin_open(*args, **kwargs))))
        stack.enter_context(patch("tempfile.TemporaryFile", lambda *args, **kwargs: self.wrap(temporary(*args, **kwargs))))

        def traced_sync(fd):
            self.operation("fsync", "descriptor")
            return sync(fd)

        def traced_replace(source, target):
            self.operation("replace", Path(target).name)
            return replace(source, target)

        stack.enter_context(patch("os.fsync", traced_sync))
        stack.enter_context(patch("os.replace", traced_replace))


def write_pcm(path: Path, amplitude: int = 1000, frames: int = 8000, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setparams((channels, 2, 8000, 0, "NONE", "not compressed"))
        for start in range(0, frames, 4096):
            block = b"".join(struct.pack("<" + "h" * channels, *([(amplitude if i >= frames * 2 // 5 else amplitude // 100) * (1 if i % 2 else -1)] * channels))
                             for i in range(start, min(frames, start + 4096)))
            writer.writeframesraw(block)


def result_hashes(result):
    return tuple("sha256:" + sha256(path.read_bytes()).hexdigest()
                 for path in (result.source_path, result.dialogue_path, result.background_path))


class PublicationTests(unittest.TestCase):
    def assert_valid(self, result):
        self.assertEqual(read_result(result.to_dict()), result)
        self.assertEqual(result_hashes(result), (result.metrics.source_hash, result.metrics.dialogue_hash, result.metrics.background_hash))

    def test_every_replace_failure_preserves_previous_result_hashes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            backend = CpuAttenuationBackend()
            replace = os.replace
            with patch("os.replace", wraps=replace) as calls:
                backend.process(source, root / "count")
            for failed_call in range(1, calls.call_count + 1):
                with self.subTest(replace=failed_call):
                    write_pcm(source, 1000)
                    previous = backend.process(source, root / str(failed_call))
                    hashes = result_hashes(previous)
                    write_pcm(source, 9000)
                    seen = 0

                    def fail_at(source_path, target):
                        nonlocal seen
                        seen += 1
                        if seen == failed_call:
                            raise PermissionError("injected replace failure")
                        return replace(source_path, target)

                    with patch("os.replace", side_effect=fail_at):
                        with self.assertRaises((OSError, ValueError)):
                            backend.process(source, root / str(failed_call))
                    self.assertEqual(result_hashes(previous), hashes)

    def test_every_read_write_flush_sync_replace_failure(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source, frames=128)
            backend = CpuAttenuationBackend()
            trace = IOTrace()
            with ExitStack() as stack:
                trace.install(stack)
                backend.process(source, root / "trace")
            commit = next(i + 1 for i, call in enumerate(trace.calls) if call == ("replace", publication.CURRENT))
            print("publication_fault_positions=" + str(len(trace.calls)), flush=True)
            self.assertEqual({kind for kind, _ in trace.calls}, {"read", "write", "flush", "fsync", "replace"})
            for position, call in enumerate(trace.calls, 1):
                with self.subTest(position=position, operation=call):
                    output = root / str(position)
                    write_pcm(source, 1000, frames=128)
                    previous = backend.process(source, output)
                    hashes = result_hashes(previous)
                    write_pcm(source, 9000, frames=128)
                    injected = IOTrace(position)
                    with ExitStack() as stack:
                        injected.install(stack)
                        with self.assertRaises((OSError, ValueError)):
                            backend.process(source, output)
                    self.assertTrue(injected.hit)
                    self.assertEqual(result_hashes(previous), hashes)
                    current = load_current(output)
                    self.assert_valid(current)
                    if position <= commit:
                        self.assertEqual(current, previous)
                    else:
                        self.assertNotEqual(current.source_path, previous.source_path)

    def test_success_keeps_both_generations_and_api_fallback(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source, 1000, channels=2)
            old = CpuAttenuationBackend().process(source, root / "out")
            write_pcm(source, 9000, frames=16000, channels=2)
            new = CpuAttenuationBackend().process(source, root / "out")
            self.assertEqual(load_current(root / "out"), new)
            self.assertNotEqual(old.source_path.parent, new.source_path.parent)
            self.assert_valid(old)
            self.assert_valid(new)
            self.assertEqual(new.metrics.frame_count, 16000)
            self.assertEqual(new.metrics.channels, 2)
            self.assertEqual(new.metrics.sample_rate, 8000)
            self.assertNotEqual(old.metrics.dialogue_hash, new.metrics.dialogue_hash)
            self.assertNotEqual(old.metrics.background_hash, new.metrics.background_hash)
            self.assertEqual(new.to_dict()["schema_version"], 2)
            self.assertTrue(new.to_dict()["fallback"])
            self.assertIsNone(new.metrics.residual_speech_millidb)
            self.assertIsNone(new.metrics.background_artifact_milli)
            self.assertEqual(new.metrics.decision, "AUD-0")

    def test_legacy_recorded_result_is_readable_and_never_overwritten(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            previous = CpuAttenuationBackend().process(source, root / "producer")
            legacy = root / "legacy"
            legacy.mkdir()
            document = previous.to_dict()
            for key, name in zip(("source_path", "dialogue_path", "background_path"), publication.NAMES):
                shutil.copyfile(document[key], legacy / name)
                document[key] = str(legacy / name)
            old = read_result(document)
            self.assertIsNone(load_current(legacy))
            write_pcm(source, 9000)
            new = CpuAttenuationBackend().process(source, legacy)
            self.assert_valid(old)
            self.assertEqual(read_result(document), old)
            self.assertEqual(load_current(legacy), new)

    def test_corrupt_missing_metadata_mismatch_and_partial_files_fail_closed(self):
        mutations = ("missing", "header", "truncated", "payload", "metadata", "manifest", "pointer")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    out = root / mutation
                    result = CpuAttenuationBackend().process(source, out)
                    if mutation == "missing":
                        result.background_path.unlink()
                    elif mutation == "header":
                        result.dialogue_path.write_bytes(b"bad wav")
                    elif mutation == "truncated":
                        with result.source_path.open("r+b") as stream:
                            stream.truncate(50)
                    elif mutation == "payload":
                        with result.source_path.open("r+b") as stream:
                            stream.seek(60)
                            stream.write(b"\xff\xff")
                    elif mutation == "metadata":
                        write_pcm(result.background_path, frames=4000, channels=2)
                    elif mutation == "manifest":
                        (result.source_path.parent / "result.json").write_text("{}")
                    else:
                        (out / publication.CURRENT).write_text('{"generation":"../escape"}')
                    (out / ".current-audio.orphan.partial").write_bytes(b"unfinished")
                    with self.assertRaises((OSError, ValueError, KeyError, EOFError, wave.Error)):
                        load_current(out)
            orphaned = root / "orphans"
            (orphaned / publication.STORE / ".ab.partial").mkdir(parents=True)
            (orphaned / ".current-audio.ab.partial").write_text("{}")
            self.assertIsNone(load_current(orphaned))

    def test_manifest_cannot_bless_mismatched_metadata_or_truncated_pcm(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            result = CpuAttenuationBackend().process(source, root / "out")
            report = result.to_dict()
            report["metrics"]["frame_count"] += 1
            with self.assertRaisesRegex(ValueError, "metadata"):
                read_result(report)
            report = result.to_dict()
            with result.source_path.open("r+b") as stream:
                stream.truncate(100)
            report["metrics"]["source_hash"] = result_hashes(result)[0]
            with self.assertRaisesRegex(ValueError, "frame count"):
                read_result(report)

    def test_invalid_candidates_cannot_replace_current(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, out = root / "input.wav", root / "out"
            write_pcm(source)
            old = CpuAttenuationBackend().process(source, out)
            publish = publication.publish
            for mutation in ("missing", "corrupt", "metadata", "truncated"):
                with self.subTest(mutation=mutation):
                    def invalid(pending, final, result):
                        target = pending / "background_stem.wav"
                        if mutation == "missing":
                            target.unlink()
                        elif mutation == "corrupt":
                            target.write_bytes(b"bad")
                        elif mutation == "metadata":
                            write_pcm(target, frames=4000)
                        else:
                            with target.open("r+b") as stream:
                                stream.truncate(60)
                        publish(pending, final, result)
                    with patch.object(publication, "publish", side_effect=invalid):
                        with self.assertRaises((OSError, ValueError, EOFError, wave.Error)):
                            CpuAttenuationBackend().process(source, out)
                    self.assertEqual(load_current(out), old)
                    self.assert_valid(old)

    def test_short_document_write_cannot_publish(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, out = root / "input.wav", root / "out"
            write_pcm(source)
            old = CpuAttenuationBackend().process(source, out)
            for selected in ("result.json", "pointer"):
                with self.subTest(document=selected):
                    path_open = Path.open

                    def short_open(path, *args, **kwargs):
                        stream = path_open(path, *args, **kwargs)
                        if args and args[0] == "xb" and (path.name == selected or selected == "pointer" and path.name.startswith(".current-audio.")):
                            wrapped = IOTrace().wrap(stream)
                            wrapped.write = lambda data: stream.write(data[:len(data) // 2])
                            return wrapped
                        return stream

                    with patch.object(Path, "open", short_open):
                        with self.assertRaisesRegex(OSError, "short"):
                            CpuAttenuationBackend().process(source, out)
                    self.assertEqual(load_current(out), old)
                    self.assert_valid(old)

    def test_disk_percentile_matches_historical_sorted_selection(self):
        import random
        from engine.dubflow.separation.production import _noise_floor
        rng = random.Random(209)
        for count in (0, 1, 5, 4097, 12000):
            values = [rng.choice((0.0, rng.random() * 32768, 123.5)) for _ in range(count)]
            positive = sorted(value for value in values if value > 0)
            expected = positive[len(positive) // 5] if positive else 0.0
            with tempfile.TemporaryFile(mode="w+b") as stream:
                for value in values:
                    stream.write(struct.pack(">d", value))
                self.assertEqual(_noise_floor(stream), expected)

    def test_source_mutation_between_passes_does_not_publish(self):
        from engine.dubflow.separation import production
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            write_pcm(source)
            previous = CpuAttenuationBackend().process(source, root / "out")
            iterator = production._iter_pcm
            count = 0

            def mutate(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    write_pcm(source, 9000)
                yield from iterator(*args, **kwargs)

            with patch.object(production, "_iter_pcm", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "AUDIO_INPUT_CHANGED"):
                    CpuAttenuationBackend().process(source, root / "out")
            self.assertEqual(load_current(root / "out"), previous)
            self.assert_valid(previous)

    def test_large_wav_has_bounded_reads_writes_and_analysis_memory(self):
        import tracemalloc
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            # > 1MiB forces multiple hash and PCM I/O blocks.
            write_pcm(source, frames=300_000, channels=2)
            trace = IOTrace()
            tracemalloc.start()
            try:
                with ExitStack() as stack:
                    trace.install(stack)
                    result = CpuAttenuationBackend().process(source, root / "out")
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLessEqual(trace.max_read, 1024 * 1024)
            self.assertLessEqual(trace.max_write, 64 * 1024)
            self.assertLess(peak, 12 * 1024 * 1024)
            print(f"publication_resource_bytes input={source.stat().st_size} peak_tracemalloc={peak} max_read={trace.max_read} max_write={trace.max_write}", flush=True)
            self.assert_valid(result)

    def test_real_child_termination_and_fresh_process_recovery(self):
        milestones = ("flushed-source_audio.wav", "flushed-dialogue_stem.wav", "flushed-background_stem.wav",
                      "flushed-result.json", "before-seal", "after-seal", "flushed-pointer", "before-current", "after-current")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.wav"
            for milestone in milestones:
                with self.subTest(milestone=milestone):
                    out, marker = root / milestone, root / (milestone + ".marker")
                    write_pcm(source, 1000, frames=512)
                    old = CpuAttenuationBackend().process(source, out)
                    write_pcm(source, 9000, frames=512)
                    fixture = str(Path(__file__).with_name("publication_fixture.py"))
                    child = subprocess.Popen([sys.executable, "-B", fixture, "run", str(source), str(out), str(marker), milestone], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        deadline = time.monotonic() + 15
                        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertTrue(marker.exists(), "fixture did not reach " + milestone)
                        child.kill()  # actual external process termination, not an exception
                        child.communicate(timeout=10)
                        self.assertNotEqual(child.returncode, 0)
                    finally:
                        if child.poll() is None:
                            child.kill()
                        child.communicate(timeout=10)
                    recovered = subprocess.run([sys.executable, "-B", fixture, "recover", str(out)], capture_output=True, text=True, timeout=10, check=True)
                    report = json.loads(recovered.stdout)
                    current = read_result(report)
                    self.assert_valid(old)
                    if milestone == "after-current":
                        self.assertNotEqual(current.source_path, old.source_path)
                        with wave.open(str(current.source_path), "rb") as reader:
                            self.assertEqual(struct.unpack("<h", reader.readframes(1))[0], -90)
                    else:
                        self.assertEqual(current, old)
                    # Retry creates a fresh generation; never adopts the orphan.
                    retry = CpuAttenuationBackend().process(source, out)
                    self.assert_valid(retry)
                    self.assert_valid(old)

    @unittest.skipUnless(os.name == "nt", "NOT_RUN: Windows native sharing/rename semantics")
    def test_windows_native_pointer_lock_and_retained_reader(self):
        import ctypes
        from ctypes import wintypes
        create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source, out = root / "input.wav", root / "out"
            write_pcm(source)
            old = CpuAttenuationBackend().process(source, out)
            write_pcm(source, 9000)
            for locked in (out / publication.CURRENT, old.background_path):
                with self.subTest(locked=locked.name):
                    handle = create(str(locked), 0x80000000, 0, None, 3, 0x80, None)
                    self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
                    try:
                        if locked.name == publication.CURRENT:
                            with self.assertRaises(PermissionError):
                                CpuAttenuationBackend().process(source, out)
                        else:
                            new = CpuAttenuationBackend().process(source, out)
                            self.assert_valid(new)
                    finally:
                        self.assertTrue(close(handle))
                    self.assert_valid(old)


if __name__ == "__main__":
    unittest.main()

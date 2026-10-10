"""Installer observer contention: real Windows handles, verified staging."""
from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from packaging.release import bootstrap
from packaging.release.builder import build_bundle
from packaging.release.manifest import hash_file
from test_bundle import _runtime, SOURCE_SHA, SOURCE_DATE_EPOCH


@contextmanager
def deny_delete_reader(path: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    # Read/write sharing permits ordinary observers but rejects replacement.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class InstallerProgressTests(unittest.TestCase):
    def make_bundle(self, root: Path, *, extra_files: int = 0):
        source = root / "source"
        for directory in ("apps/desktop", "contracts", "engine", "minimal-pipeline-wiring",
                "models", "packaging", "docs/licenses"):
            (source / directory).mkdir(parents=True)
        (source / "README.md").write_text("Installer payload fixture\n", encoding="utf-8")
        runtime = root / "runtime"
        _runtime(runtime)
        for index in range(extra_files):
            (runtime / "Lib" / f"part-{index:04d}.py").write_text("# payload\n", encoding="utf-8")
        return build_bundle(source_root=source, output_dir=root / "bundle", version="0.1.0-rc1",
            source_sha=SOURCE_SHA, runtime_root=runtime, source_date_epoch=SOURCE_DATE_EPOCH)

    def test_many_files_bound_snapshot_writes_without_losing_verified_payload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root, extra_files=257)
            destination = root / "install"
            # Freeze time so only the file-count cadence determines snapshots.
            with mock.patch.object(bootstrap.time, "monotonic", return_value=10.0), \
                    mock.patch.object(bootstrap, "_write_progress", wraps=bootstrap._write_progress) as writes:
                result = bootstrap.install_bundle(bundle.staging_dir, destination)
            self.assertTrue(result["ready"])
            count = len(bundle.manifest.artifacts) + 1
            self.assertLessEqual(writes.call_count, count // 64 + 2)
            last = writes.call_args.args[1]
            self.assertEqual(len(last["completed"]), count)
            bootstrap.verify_bundle(destination / "versions/0.1.0-rc1", bundle.manifest)

    def test_disk_failure_in_progress_remains_fatal_before_activation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root)
            destination = root / "install"
            with mock.patch.object(bootstrap, "_atomic_write", side_effect=OSError(errno.ENOSPC, "disk full")):
                with self.assertRaises(OSError) as raised:
                    bootstrap.install_bundle(bundle.staging_dir, destination)
            self.assertEqual(raised.exception.errno, errno.ENOSPC)
            self.assertFalse((destination / "current.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows deny-delete reader handles")
    def test_progress_reader_cannot_abort_verified_install_or_resume(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root, extra_files=70)
            destination = root / "install"
            progress = destination / "install-progress-0.1.0-rc1.json"
            copy = bootstrap._copy_bundle
            locks = []
            def copy_with_observer(*args, **kwargs):
                reader = deny_delete_reader(progress)
                reader.__enter__()
                locks.append(reader)
                return copy(*args, **kwargs)
            try:
                with mock.patch.object(bootstrap, "_copy_bundle", side_effect=copy_with_observer):
                    result = bootstrap.install_bundle(bundle.staging_dir, destination)
                self.assertTrue(result["ready"])
                self.assertTrue(progress.exists())
                self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))["completed"], [])
                version = destination / "versions/0.1.0-rc1"
                bootstrap.verify_bundle(version, bundle.manifest)
                pointer = json.loads((destination / "current.json").read_text(encoding="utf-8"))
                self.assertEqual(pointer["manifest_sha256"], hash_file(version / "release-manifest.json"))
                self.assertFalse(list(destination.glob(".*.partial")))
            finally:
                for reader in locks: reader.__exit__(None, None, None)
            # After the observer closes, idempotent recovery cleans its stale
            # snapshot and still revalidates every installed payload file.
            second = bootstrap.install_bundle(bundle.staging_dir, destination)
            self.assertTrue(second["ready"])
            self.assertFalse(progress.exists())
            self.assertEqual(result["manifest_sha256"], second["manifest_sha256"])

    @unittest.skipUnless(os.name == "nt", "Windows deny-delete reader handles")
    def test_interrupted_copy_reuses_verified_files_missing_from_locked_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root, extra_files=70)
            destination = root / "install"
            progress = destination / "install-progress-0.1.0-rc1.json"
            copy_bundle = bootstrap._copy_bundle
            copy_file = bootstrap.shutil.copy2
            locks, copied = [], []
            def interrupted_file_copy(source, target):
                if len(copied) == 9:
                    raise KeyboardInterrupt("deliberate interrupted installation")
                copy_file(source, target)
                copied.append(Path(source).relative_to(bundle.staging_dir).as_posix())
            def copy_with_observer(*args, **kwargs):
                reader = deny_delete_reader(progress)
                reader.__enter__()
                locks.append(reader)
                return copy_bundle(*args, **kwargs)
            try:
                with mock.patch.object(bootstrap.time, "monotonic", return_value=10.0), \
                        mock.patch.object(bootstrap, "_copy_bundle", side_effect=copy_with_observer), \
                        mock.patch.object(bootstrap.shutil, "copy2", side_effect=interrupted_file_copy):
                    with self.assertRaises(KeyboardInterrupt):
                        bootstrap.install_bundle(bundle.staging_dir, destination)
                self.assertFalse((destination / "current.json").exists())
                self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))["completed"], [])
                staging = destination / "versions/.0.1.0-rc1.staging"
                before = {name: ((staging / name).stat().st_mtime_ns, hash_file(staging / name)) for name in copied}
            finally:
                for reader in locks: reader.__exit__(None, None, None)
            with mock.patch.object(bootstrap.shutil, "copy2", wraps=copy_file) as copies:
                result = bootstrap.install_bundle(bundle.staging_dir, destination)
            self.assertTrue(result["ready"])
            copied_again = {Path(call.args[0]).relative_to(bundle.staging_dir).as_posix() for call in copies.call_args_list}
            self.assertTrue(set(copied).isdisjoint(copied_again))
            version = destination / "versions/0.1.0-rc1"
            self.assertEqual(before, {name: ((version / name).stat().st_mtime_ns, hash_file(version / name)) for name in copied})
            bootstrap.verify_bundle(version, bundle.manifest)
            self.assertFalse(progress.exists())

    @unittest.skipUnless(os.name == "nt", "Windows deny-delete reader handles")
    def test_authoritative_pointer_lock_still_fails_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            path.write_bytes(b'{}\n')
            with deny_delete_reader(path):
                with self.assertRaises(PermissionError):
                    bootstrap._atomic_write(path, b'{"changed":true}\n')
            self.assertEqual(path.read_bytes(), b'{}\n')
            self.assertEqual(list(path.parent.glob("*.partial")), [])


if __name__ == "__main__":
    unittest.main()

"""The stems folder, held open by handle: every write, publication and removal there is relative to it.

``OutDir.open(path)`` opens the folder's parent's parent (the editor's draft folder's parent) by path,
then the draft folder and the stems folder one step at a time *relative to the folder above*, refusing
a symlink, junction or any other reparse point at each step. After that nothing goes through the path
again:

* POSIX: ``dir_fd``-relative ``os.open`` / ``os.replace`` / ``os.unlink`` with ``O_NOFOLLOW``.
* Windows: ``NtCreateFile`` with ``RootDirectory`` set to the folder handle (and
  ``FILE_OPEN_REPARSE_POINT``); a temporary file is published by renaming *its own handle* to a plain
  name in its own folder (``FileRenameInformation``, no root, no path) and removed by its own handle
  (``FileDispositionInformation``).

So if the stems folder (or a folder above it) is renamed and its path swapped for a link or junction
while a split runs, every later operation still lands in the real folder that was opened, never outside.
"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
import sys


class UnsafeFolder(ValueError):
    pass


UNSAFE = "thư mục tách không an toàn (là liên kết hoặc không phải thư mục)"
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _plain_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name or "\0" in name:
        raise UnsafeFolder(UNSAFE)
    return name


def temp_name(prefix: str, suffix: str) -> str:
    """A fresh name for a temporary file (tests replace it to aim a collision)."""
    return f"{prefix}{secrets.token_hex(8)}{suffix}"


class _Temp:
    """A temporary file made inside the folder: ``file`` to write, then ``publish`` or ``discard``."""

    def __init__(self, folder: "OutDir", name: str, file, handle=None) -> None:
        self.folder, self.name, self.file, self._handle = folder, name, file, handle
        self._done = False

    def publish(self, target: str) -> None:
        self.file.flush()
        self.folder._publish(self, _plain_name(target))
        self._done = True
        self.file.close()

    def discard(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.folder._discard(self)
        finally:
            try:
                self.file.close()
            except OSError:
                pass


if sys.platform != "win32":

    class OutDir:
        def __init__(self, fd: int, path: Path) -> None:
            self.fd, self.path = fd, path

        @classmethod
        def open(cls, path: Path, *, make: bool = False) -> "OutDir":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            opened = []
            try:
                try:
                    opened.append(os.open(path.parent.parent, flags))
                    opened.append(os.open(_plain_name(path.parent.name), flags, dir_fd=opened[-1]))
                    name = _plain_name(path.name)
                    if make:
                        try:
                            os.mkdir(name, 0o777, dir_fd=opened[-1])
                        except FileExistsError:
                            pass
                    fd = os.open(name, flags, dir_fd=opened[-1])
                except OSError:
                    raise UnsafeFolder(UNSAFE) from None
                if not stat.S_ISDIR(os.fstat(fd).st_mode):
                    os.close(fd)
                    raise UnsafeFolder(UNSAFE)
                return cls(fd, path)
            finally:
                for fd_ in opened:
                    os.close(fd_)

        def close(self) -> None:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1

        def create(self, prefix: str, suffix: str, mode: str = "wb") -> _Temp:
            name = _plain_name(temp_name(prefix, suffix))
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o644,
                         dir_fd=self.fd)
            return _Temp(self, name, os.fdopen(fd, mode, **({} if "b" in mode else {"encoding": "utf-8"})))

        def _publish(self, temp: _Temp, target: str) -> None:
            os.replace(temp.name, target, src_dir_fd=self.fd, dst_dir_fd=self.fd)

        def _discard(self, temp: _Temp) -> None:
            self.unlink(temp.name)

        def unlink(self, name: str) -> None:
            try:
                os.unlink(_plain_name(name), dir_fd=self.fd)
            except FileNotFoundError:
                pass

        def exists(self, name: str) -> bool:
            try:
                os.stat(_plain_name(name), dir_fd=self.fd, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False

        def __enter__(self) -> "OutDir":
            return self

        def __exit__(self, *_exc) -> None:
            self.close()

else:  # pragma: no cover - exercised by the Windows CI lane
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _ntdll = ctypes.WinDLL("ntdll")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", wintypes.LPWSTR)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(_UnicodeString)), ("Attributes", wintypes.ULONG),
                    ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                    ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                    ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                    ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                    ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]

    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(_ObjectAttributes),
                                    ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                                    wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long
    _ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                                            wintypes.ULONG, ctypes.c_int]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
                                          wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]

    _INVALID = wintypes.HANDLE(-1).value
    DELETE, SYNCHRONIZE = 0x10000, 0x100000
    FILE_READ_ATTRIBUTES, FILE_LIST_DIRECTORY, FILE_TRAVERSE, FILE_ADD_FILE = 0x80, 0x1, 0x20, 0x2
    GENERIC_WRITE = 0x40000000
    SHARE_ALL = 0x7
    FILE_OPEN, FILE_CREATE, FILE_OPEN_IF = 1, 2, 3
    FILE_DIRECTORY_FILE, FILE_NON_DIRECTORY_FILE = 0x1, 0x40
    FILE_SYNCHRONOUS_IO_NONALERT, FILE_OPEN_REPARSE_POINT = 0x20, 0x200000
    OBJ_CASE_INSENSITIVE = 0x40
    FILE_RENAME_INFORMATION, FILE_DISPOSITION_INFORMATION = 10, 13
    STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034 - (1 << 32)
    STATUS_OBJECT_PATH_NOT_FOUND = 0xC000003A - (1 << 32)
    STATUS_OBJECT_NAME_COLLISION = 0xC0000035 - (1 << 32)
    DIR_ACCESS = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_ADD_FILE | FILE_READ_ATTRIBUTES | SYNCHRONIZE

    def _check(status: int, name: str) -> None:
        if status < 0:
            error = _ntdll.RtlNtStatusToDosError(status)
            if status in (STATUS_OBJECT_NAME_NOT_FOUND, STATUS_OBJECT_PATH_NOT_FOUND):
                raise FileNotFoundError(error, f"not found: {name}")
            if status == STATUS_OBJECT_NAME_COLLISION:
                raise FileExistsError(error, f"exists: {name}")
            raise OSError(error, f"NTSTATUS {status & 0xFFFFFFFF:#010x}: {name}")

    def _open_at(root, name: str, access: int, disposition: int, options: int, attributes: int = 0):
        """``name`` (one plain component) relative to the folder handle ``root``; a link there is opened itself."""
        name = _plain_name(name)
        buffer = ctypes.create_unicode_buffer(name)
        text = _UnicodeString(len(name) * 2, len(name) * 2 + 2, ctypes.cast(buffer, wintypes.LPWSTR))
        attrs = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), root, ctypes.pointer(text), OBJ_CASE_INSENSITIVE,
                                  None, None)
        handle, io = wintypes.HANDLE(), _IoStatusBlock()
        _check(_ntdll.NtCreateFile(ctypes.byref(handle), access | SYNCHRONIZE, ctypes.byref(attrs), ctypes.byref(io), None,
                                   attributes, SHARE_ALL, disposition,
                                   options | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_REPARSE_POINT, None, 0), name)
        return handle.value

    def _attributes(handle) -> int:
        info = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.dwFileAttributes

    def _plain_dir_handle(handle) -> None:
        attributes = _attributes(handle)
        if attributes & FILE_ATTRIBUTE_REPARSE_POINT or not attributes & FILE_ATTRIBUTE_DIRECTORY:
            raise UnsafeFolder(UNSAFE)

    def _set_info(handle, data, kind: int, name: str) -> None:
        io = _IoStatusBlock()
        _check(_ntdll.NtSetInformationFile(handle, ctypes.byref(io), ctypes.addressof(data), ctypes.sizeof(data), kind), name)

    def _rename(handle, target: str) -> None:
        """Rename the file behind ``handle`` to ``target`` in its own folder (no root, no path: never elsewhere)."""
        size = len(target) * 2

        class _Rename(ctypes.Structure):
            _fields_ = [("ReplaceIfExists", wintypes.BOOLEAN), ("RootDirectory", wintypes.HANDLE),
                        ("FileNameLength", wintypes.ULONG), ("FileName", wintypes.WCHAR * (len(target) + 1))]
        info = _Rename(True, None, size, target)
        _set_info(handle, info, FILE_RENAME_INFORMATION, target)

    def _delete(handle, name: str) -> None:
        class _Disposition(ctypes.Structure):
            _fields_ = [("DeleteFile", wintypes.BOOLEAN)]
        _set_info(handle, _Disposition(True), FILE_DISPOSITION_INFORMATION, name)

    class OutDir:
        def __init__(self, handle, path: Path) -> None:
            self.handle, self.path = handle, path

        @classmethod
        def open(cls, path: Path, *, make: bool = False) -> "OutDir":
            top = _kernel32.CreateFileW(str(path.parent.parent), DIR_ACCESS, SHARE_ALL, None, 3,
                                        0x02000000 | 0x00200000, None)  # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            if top in (None, _INVALID):
                raise UnsafeFolder(UNSAFE)
            opened = [top]
            try:
                _plain_dir_handle(top)
                try:
                    middle = _open_at(top, path.parent.name, DIR_ACCESS, FILE_OPEN, FILE_DIRECTORY_FILE)
                except OSError:
                    raise UnsafeFolder(UNSAFE) from None
                opened.append(middle)
                _plain_dir_handle(middle)
                try:
                    handle = _open_at(middle, path.name, DIR_ACCESS, FILE_OPEN_IF if make else FILE_OPEN, FILE_DIRECTORY_FILE)
                except OSError:
                    raise UnsafeFolder(UNSAFE) from None
                try:
                    _plain_dir_handle(handle)
                except BaseException:
                    _kernel32.CloseHandle(handle)
                    raise
                return cls(handle, path)
            finally:
                for handle_ in opened:
                    _kernel32.CloseHandle(handle_)

        def close(self) -> None:
            if self.handle:
                _kernel32.CloseHandle(self.handle)
                self.handle = None

        def create(self, prefix: str, suffix: str, mode: str = "wb") -> _Temp:
            name = temp_name(prefix, suffix)
            handle = _open_at(self.handle, name, GENERIC_WRITE | DELETE | FILE_READ_ATTRIBUTES, FILE_CREATE,
                              FILE_NON_DIRECTORY_FILE, 0x80)
            # A second handle for the writer (the file object owns and closes it); the first renames/deletes.
            duplicate = wintypes.HANDLE()
            process = _kernel32.GetCurrentProcess()
            if not _kernel32.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                _kernel32.CloseHandle(handle)
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY | getattr(os, "O_NOINHERIT", 0))
            file = os.fdopen(fd, mode, **({} if "b" in mode else {"encoding": "utf-8"}))
            return _Temp(self, name, file, handle)

        def _publish(self, temp: _Temp, target: str) -> None:
            _rename(temp._handle, target)
            _kernel32.CloseHandle(temp._handle)
            temp._handle = None

        def _discard(self, temp: _Temp) -> None:
            try:
                _delete(temp._handle, temp.name)
            finally:
                _kernel32.CloseHandle(temp._handle)
                temp._handle = None

        def unlink(self, name: str) -> None:
            try:
                handle = _open_at(self.handle, name, DELETE | FILE_READ_ATTRIBUTES, FILE_OPEN, FILE_NON_DIRECTORY_FILE)
            except FileNotFoundError:
                return
            try:
                _delete(handle, name)
            finally:
                _kernel32.CloseHandle(handle)

        def exists(self, name: str) -> bool:
            try:
                handle = _open_at(self.handle, name, FILE_READ_ATTRIBUTES, FILE_OPEN, 0)
            except FileNotFoundError:
                return False
            _kernel32.CloseHandle(handle)
            return True

        def __enter__(self) -> "OutDir":
            return self

        def __exit__(self, *_exc) -> None:
            self.close()

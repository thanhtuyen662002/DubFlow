"""Own a native process tree until its job worker closes or disappears."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import uuid

from .adapter import TtsBackendError


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "read_operations", "write_operations", "other_operations",
        "read_bytes", "write_bytes", "other_bytes",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class WindowsJob:
    """A non-inherited kill-on-close Job Object, assigned before inference."""

    def __init__(self, process) -> None:
        self.handle = None
        self.name = None
        if os.name != "nt":
            return
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.name = "DubFlowNativeTts-" + uuid.uuid4().hex
        self.handle = self.api.CreateJobObjectW(None, self.name)
        try:
            if not self.handle:
                self._failed("create")
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self._failed("configure")
            # CPython's live process handle avoids PID reuse between spawning
            # and assignment. The shipped interpreter is pinned CPython.
            if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
                if process.poll() is not None:
                    raise TtsBackendError("TTS_NATIVE_EXITED", "native process exited before job assignment")
                self._failed("assign")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _failed(operation: str) -> None:
        error = ctypes.get_last_error()
        raise TtsBackendError("TTS_NATIVE_CONTAINMENT_FAILED", f"Windows native job {operation} failed ({error})")

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                self._failed("close")

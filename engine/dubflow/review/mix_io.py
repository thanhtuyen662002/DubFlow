"""Bounded recovery of a native-confined Windows journal replacement refusal.

The producer re-admits its existing generation/checkpoint on every retry. An
observed delete-sharing probe must change the refused condition first. No ACL,
permissions, model, producer, checkpoint or prior artifact is repaired here.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time
from typing import Callable, TypeVar

from engine.dubflow.mix import streaming
from engine.dubflow.mix.adapter import MixError

T = TypeVar('T')
MAX_ATTEMPTS = 3
PROBE_DELAYS = (0.0, 0.025, 0.05, 0.1, 0.1, 0.1, 0.1, 0.1)


def _journal_target(error: MixError, root: Path) -> Path | None:
    cause = error.__cause__
    if (error.code != 'MIX_IO_FAILED' or not isinstance(cause, OSError)
            or getattr(cause, 'winerror', None) not in (5, 32)
            or not isinstance(cause.filename, str) or not isinstance(getattr(cause, 'filename2', None), str)):
        return None
    temporary, destination = Path(cause.filename), Path(cause.filename2)
    if (not temporary.is_absolute() or not destination.is_absolute()
            or '..' in temporary.parts or '..' in destination.parts
            or temporary != destination.with_name('checkpoint.json.partial')
            or destination.name != 'checkpoint.json' or destination.parent.parent != root
            or not re.fullmatch(r'stream-[a-f0-9]{64}', destination.parent.name)):
        return None
    streaming._plain(root); streaming._plain(temporary); streaming._plain(destination)
    if not temporary.is_file() or not destination.is_file():
        return None
    return destination


def _delete_available(path: Path) -> bool:
    if os.name != 'nt': return False
    import ctypes
    from ctypes import wintypes
    raw = str(path)
    if not raw.startswith('\\\\?\\'):
        raw = '\\\\?\\UNC\\' + raw[2:] if raw.startswith('\\\\') else '\\\\?\\' + raw
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    api.GetFileAttributesW.restype = wintypes.DWORD
    attributes = api.GetFileAttributesW(raw)
    if attributes == 0xFFFFFFFF or attributes & (0x1 | 0x10 | 0x400):
        return False
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    # DELETE access, SHARE_READ|WRITE|DELETE, OPEN_EXISTING, OPEN_REPARSE_POINT.
    # Opening grants no mutation: close before the producer re-admits its input.
    handle = api.CreateFileW(raw, 0x10000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value: return False
    return bool(api.CloseHandle(handle))


def recover_mix_io(operation: Callable[[], T], *, generation_root: Path,
                   ready_probe: Callable[[Path], bool] = _delete_available) -> T:
    root = Path(generation_root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('mix recovery requires the native composition generation root')
    for attempt in range(MAX_ATTEMPTS):
        try:
            return operation()
        except MixError as error:
            target = _journal_target(error, root)
            if target is None or attempt + 1 == MAX_ATTEMPTS:
                raise
            # Waiting alone is not a retry condition. Require an observed change
            # from refused replacement to currently granted delete-sharing.
            for delay in PROBE_DELAYS:
                if delay: time.sleep(delay)
                if (ready_probe(target.with_name('checkpoint.json.partial'))
                        and ready_probe(target)): break
            else:
                raise
    raise AssertionError('unreachable bounded mix retry')

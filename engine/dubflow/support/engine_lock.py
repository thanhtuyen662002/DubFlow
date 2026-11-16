"""The engine's hold on its run (Phase 8, F3): proof for the desktop host that the engine is still alive.

The host writes ``status.json`` RUNNING before it starts the engine and settles it when the engine exits. When the
app itself dies (or the machine stops) while the engine dies too, nothing settled it: the run stayed RUNNING for
ever. The engine therefore holds an OS lock on ``<work>/engine.lock`` for its whole life. The OS releases it when the
process ends, however it ends, so a later app that can take the lock knows the engine is gone (and a held lock means
it is still working, even after the app restarted). A second engine on the same run finds the lock held and stops
without touching the run, so the same run is never made twice at once.

Windows: ``msvcrt.locking`` on byte 0 (overlaps the host's whole-file ``LockFileEx``); elsewhere ``fcntl.flock``.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

LOCK_NAME = "engine.lock"
_HELD: dict[str, int] = {}  # run -> descriptor, open (and the lock held) until the process ends


class EngineBusy(RuntimeError):
    """Another engine holds this run."""


def hold(work: Path) -> None:
    """Take the run's engine lock for the life of this process, or raise ``EngineBusy``."""
    work.mkdir(parents=True, exist_ok=True)
    path = work / LOCK_NAME
    key = os.path.normcase(os.path.abspath(path))
    if key in _HELD:
        return  # this process already holds it
    if path.is_symlink():
        raise EngineBusy("engine lock is a link")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0), 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        raise EngineBusy("another engine is working on this run") from error
    _HELD[key] = fd

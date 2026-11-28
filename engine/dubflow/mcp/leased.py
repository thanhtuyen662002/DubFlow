"""Run one of a finished video's makers (Shorts, Đổi khung, Gói đăng bài) the way the app does (#1000).

The app holds ``<work>\\lease`` (a file opened without sharing, work_lease.rs) for the engine's whole life, so a second
start on the same video is refused, and settles the status when the engine stops without a final state. An MCP start
has no app process to do that, so this small runner does both:

    python -B -m engine.dubflow.mcp.leased --kind shorts --work <work> -- <python> -B -m engine.dubflow.shorts ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from engine.dubflow.docu.project import write_json

ERROR_SHARING_VIOLATION = 32
FINAL = ("COMPLETED", "FAILED", "CANCELLED")
# The app's own words when an engine stops without a final state (shorts.rs, reframe.rs, package.rs).
STOPPED = {
    "shorts": ("Đã hủy cắt Shorts.", "Bộ cắt Shorts đã dừng. Hãy thử lại."),
    "reframe": ("Đã hủy đổi khung 16:9.", "Bộ đổi khung đã dừng. Hãy thử lại."),
    "package": ("Đã hủy đóng gói.", "Bộ đóng gói đã dừng. Hãy thử lại."),
}


class Busy(Exception):
    """Another holder (the app, or another MCP start) has the lease."""


class Lease:
    """``<work>\\lease`` opened without sharing; Windows releases it when this process ends, also after a crash."""

    def __init__(self, work: Path) -> None:
        self.path = work / "lease"
        self.handle: Any = None

    def __enter__(self) -> "Lease":
        if sys.platform != "win32":
            self.handle = open(self.path, "ab")  # noqa: SIM115 - held for the run
            return self
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.HANDLE]
        handle = kernel32.CreateFileW(str(self.path), 0x40000000, 0, None, 4, 0x80, None)  # GENERIC_WRITE, no sharing, OPEN_ALWAYS
        if handle in (None, wintypes.HANDLE(-1).value):
            if ctypes.get_last_error() == ERROR_SHARING_VIOLATION:
                raise Busy(str(self.path))
            raise OSError(f"cannot open {self.path}")
        self.handle = handle
        return self

    def __exit__(self, *_: Any) -> None:
        if self.handle is None:
            return
        if sys.platform != "win32":
            self.handle.close()
        else:
            import ctypes

            ctypes.WinDLL("kernel32").CloseHandle(self.handle)
        self.handle = None


def lease_free(work: Path) -> bool:
    """Whether nobody holds the lease now (the app's own start, or another MCP start, would be refused otherwise)."""
    try:
        with Lease(work):
            return True
    except Busy:
        return False


def _status(work: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((work / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def settle(work: Path, kind: str) -> None:
    """After the engine: a status without a final state becomes CANCELLED (a cancel was asked) or FAILED, keeping the
    rest of it (Shorts keeps the outputs it already published), as the app's watcher does."""
    status = _status(work)
    if status is not None and status.get("state") in FINAL:
        return
    cancelled = (work / "cancel").exists()
    value = status or {"schema_version": 1}
    value["state"] = "CANCELLED" if cancelled else "FAILED"
    value["message"] = STOPPED[kind][0 if cancelled else 1]
    write_json(work / "status.json", value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(STOPPED), required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("the engine command is missing")
    try:
        with Lease(args.work):
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            code = subprocess.run(command, cwd=os.getcwd(), stdin=subprocess.DEVNULL, creationflags=flags).returncode
            settle(args.work, args.kind)
            return code
    except Busy:
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

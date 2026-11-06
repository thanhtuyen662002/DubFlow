"""How many CPU threads DubFlow's own work may use (Cài đặt → Tài nguyên máy).

The desktop host exports the user's choice to every worker as ``DUBFLOW_THREADS``:
``all`` uses every logical processor, a number caps each stage at that many
threads, and an absent value keeps each stage's own default.
"""
from __future__ import annotations

import os

ENV = "DUBFLOW_THREADS"


def machine_threads() -> int:
    return max(1, os.cpu_count() or 1)


def configured() -> int | None:
    """The user's thread budget, or None when they kept each stage's default."""
    value = os.environ.get(ENV, "").strip().lower()
    if value == "all":
        return machine_threads()
    if value.isdigit() and int(value) > 0:
        return min(int(value), machine_threads())
    return None


def cpu_threads(default: int) -> int:
    """Threads for one CPU stage: the user's budget, else ``default`` bounded by the machine."""
    chosen = configured()
    return chosen if chosen is not None else max(1, min(default, machine_threads()))


def ffmpeg_thread_args() -> list[str]:
    """``-threads``/``-filter_threads`` for FFmpeg output; FFmpeg's own auto choice when unset."""
    chosen = configured()
    return [] if chosen is None else ["-threads", str(chosen), "-filter_threads", str(chosen)]


__all__ = ["ENV", "configured", "cpu_threads", "ffmpeg_thread_args", "machine_threads"]

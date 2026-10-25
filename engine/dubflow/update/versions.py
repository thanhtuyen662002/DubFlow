"""Small deterministic release comparator, without third-party packaging."""

from __future__ import annotations

import re


def _key(version: str) -> tuple:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-([A-Za-z0-9._+-]+))?", version)
    if match is None:
        raise ValueError("version must have a numeric dotted release and optional suffix")
    core = [int(part) for part in match.group(1).split(".")]
    while len(core) > 1 and core[-1] == 0:
        core.pop()
    suffix = match.group(2)
    rc = re.fullmatch(r"rc(\d+)", suffix or "")
    return tuple(core), suffix is None, ("rc", int(rc.group(1))) if rc else (suffix or "", 0)


def compare(a: str, b: str) -> int:
    """Compare numeric releases, rc numbers, then other suffixes lexically."""

    left, right = _key(a), _key(b)
    return (left > right) - (left < right)

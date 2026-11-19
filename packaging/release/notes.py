"""Release notes for one build: the in-app update notes and the GitHub Release body.

``docs/release/notes/<X.Y.Z>.md`` holds the notes of release ``X.Y.Z``; a candidate ``X.Y.Z-rcN`` shows the notes of
the release it prepares. A version without a notes file gets the plain line ``DubFlow <version>`` (what every build
carried before). Notes longer than the in-app updater shows (``MAX_CHARS``) fail closed rather than being cut.

``python -m packaging.release.notes --version V --source-root . --out FILE`` writes the chosen notes to ``FILE``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

MAX_CHARS = 2000  # the desktop update card shows the first 2000 characters
NOTES_DIR = Path("docs") / "release" / "notes"
_VERSION = re.compile(r"v?(\d+(?:\.\d+){1,3})(?:-[A-Za-z0-9][A-Za-z0-9._+-]{0,31})?")


class NotesError(ValueError):
    """The notes cannot be produced for this version."""


def release_of(version: str) -> str:
    """``1.2.0-rc1`` (or a tag's ``v1.2.0-rc1``) -> ``1.2.0``; refuses anything that is not a dotted version with an
    optional suffix, so no path can be built from it."""
    match = _VERSION.fullmatch(version)
    if match is None:
        raise NotesError(f"unsupported release version: {version!r}")
    return match.group(1)


def notes_for(version: str, source_root: Path) -> str:
    """The notes text for ``version``: its release's notes file, or ``DubFlow <version>`` when there is none."""
    path = source_root / NOTES_DIR / f"{release_of(version)}.md"
    if not path.is_file():
        return f"DubFlow {version}"
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").strip()  # same on every OS
    if not text:
        raise NotesError(f"release notes are empty: {path}")
    if len(text) > MAX_CHARS:
        raise NotesError(f"release notes have {len(text)} characters, more than the {MAX_CHARS} the app shows: {path}")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        text = notes_for(args.version, args.source_root)
    except NotesError as error:
        print(f"release notes: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"release notes for {args.version}: {len(text)} characters -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

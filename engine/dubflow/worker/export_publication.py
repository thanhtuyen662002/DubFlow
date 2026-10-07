"""Recoverable publication of validated exports, without SQLite mutations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

ENTRIES = ("final_vi.mp4", "captions_vi.srt", "captions_vi.ass", "qc_report.json", "editable", "job_manifest.json")


class PublicationError(ValueError):
    pass


def plain(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise PublicationError("export paths cannot contain links")


def locations(output: Path):
    output = output.absolute()
    work = output / ".dubflow-work"
    pending, previous, journal = work / "export-pending", work / "export-previous", work / "publication.json"
    for path in (output, work, pending, previous, journal):
        plain(path)
    return output, pending, previous, journal


def remove_owned(path: Path, work: Path) -> None:
    if path.resolve().parent != work.resolve() or path.name not in {"export-pending", "export-previous"}:
        raise PublicationError("cleanup is outside publication storage")
    plain(path)
    if path.exists():
        for entry in path.rglob("*"):
            plain(entry)
        shutil.rmtree(path)


def write_journal(path: Path, value: dict) -> None:
    temporary = path.with_name(".publication-" + uuid.uuid4().hex + ".partial")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(value, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover(output: Path) -> None:
    output, pending, previous, journal = locations(output)
    if not journal.exists():
        if previous.exists():
            raise PublicationError("unregistered previous export requires recovery")
        return
    with journal.open("rb") as stream:
        payload = stream.read(65537)
    if len(payload) > 65536:
        raise PublicationError("publication journal exceeds bounds")
    try:
        state = json.loads(payload)
    except ValueError as error:
        raise PublicationError("invalid publication journal JSON") from error
    if not isinstance(state, dict):
        raise PublicationError("invalid publication journal object")
    items = state.get("items")
    if type(state.get("schema_version")) is not int or state["schema_version"] != 1 or not isinstance(state.get("state"), str) or state["state"] not in {"publishing", "committed"} or not isinstance(items, list) or not items or len(items) > len(ENTRIES):
        raise PublicationError("invalid publication journal")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] not in ENTRIES or item["name"] in seen or type(item.get("had_previous")) is not bool:
            raise PublicationError("invalid publication entry")
        seen.add(item["name"])
        for parent in (output, pending, previous):
            plain(parent / item["name"])
    if state["state"] == "publishing":
        pending.mkdir(parents=True, exist_ok=True)
        for item in reversed(items):
            name = item["name"]
            destination, candidate, backup = output / name, pending / name, previous / name
            if backup.exists():
                if destination.exists():
                    if candidate.exists():
                        raise PublicationError("conflicting export recovery paths")
                    os.replace(destination, candidate)
                os.replace(backup, destination)
            elif not item["had_previous"] and not candidate.exists() and destination.exists():
                os.replace(destination, candidate)
        if previous.exists():
            previous.rmdir()
        journal.unlink()
    else:
        remove_owned(previous, journal.parent)
        remove_owned(pending, journal.parent)
        journal.unlink()


def prepare(output: Path) -> Path:
    recover(output)
    output, pending, _previous, _journal = locations(output)
    pending.mkdir(parents=True, exist_ok=True)
    for entry in pending.rglob("*"):
        plain(entry)
    return pending


def publish(output: Path) -> None:
    output, pending, previous, journal = locations(output)
    if journal.exists() or previous.exists():
        raise PublicationError("publication requires recovery")
    items = [{"name": name, "had_previous": (output / name).exists()} for name in ENTRIES if (pending / name).exists()]
    if not any(item["name"] == "job_manifest.json" for item in items):
        raise PublicationError("validated export manifest is missing")
    for item in items:
        plain(pending / item["name"])
        plain(output / item["name"])
    write_journal(journal, {"schema_version": 1, "state": "publishing", "items": items})
    try:
        previous.mkdir()
        for item in items:
            name = item["name"]
            if item["had_previous"]:
                os.replace(output / name, previous / name)
            os.replace(pending / name, output / name)
        write_journal(journal, {"schema_version": 1, "state": "committed", "items": items})
    except BaseException:
        recover(output)
        raise
    recover(output)

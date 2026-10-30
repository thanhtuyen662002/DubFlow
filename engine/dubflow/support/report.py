"""Collect a small diagnostic report for one job: reports and metadata, never media.

The desktop's "Tạo báo cáo lỗi" button runs this module; the user sends the zip
it writes. It holds the job's own JSON reports, subtitles and checkpoints, a
listing of file sizes (hard links counted once), the job status, and basic
machine facts. Video, audio, images and models are never included.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
import zipfile

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_LISTED_FILES = 50_000
# Paths inside an output folder, relative to it.
REPORTS = ("job_manifest.json", "qc_report.json", "captions_vi.srt", "editable/timeline.json", "editable/dubbing_placement.json",
           ".dubflow-work/checkpoint.json", ".dubflow-work/transcript.json", ".dubflow-work/translation.json")
WORK_DOCUMENTS = ("tts_document", "mix_document", "dubbing_placement")
MEDIA_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".wav", ".pcm", ".mp3", ".m4a", ".aac", ".flac", ".png", ".jpg", ".jpeg",
                  ".onnx", ".bin", ".npz", ".exe", ".dll"}


def _desktop() -> Path:
    """The user's real Desktop, also when OneDrive or policy redirects it."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        folder_id = (ctypes.c_byte * 16)(*bytes.fromhex("3accbfb42cdb4c42b0297fe99a87c641"))  # FOLDERID_Desktop
        pointer = wintypes.LPWSTR()
        if ctypes.windll.shell32.SHGetKnownFolderPath(folder_id, 0, None, ctypes.byref(pointer)) == 0:
            try:
                return Path(pointer.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(pointer)
    return Path.home() / "Desktop"


def _sizes(root: Path) -> tuple[list[dict], int, int]:
    """Every file with its size; the unique total counts each hard-linked file once."""
    rows, seen, unique, listed = [], set(), 0, 0
    for path in sorted(root.rglob("*")):
        if len(rows) >= MAX_LISTED_FILES:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            status = path.stat()
        except OSError:
            continue
        key = (status.st_dev, status.st_ino)
        first = key not in seen
        seen.add(key)
        listed += status.st_size
        unique += status.st_size if first else 0
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": status.st_size, "counted": first})
    return rows, unique, listed


def _machine(output_dir: Path | None) -> dict:
    info = {"windows": platform.platform(), "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", ""),
            "cpu_count": os.cpu_count()}
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
                ("total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
        status = MemoryStatus(ctypes.sizeof(MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            info["ram_gb"] = round(status.total_physical / 1024 ** 3, 1)
    if output_dir is not None and output_dir.exists():
        info["free_disk_gb"] = round(shutil.disk_usage(output_dir).free / 1024 ** 3, 1)
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            result = subprocess.run([smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            info["nvidia"] = result.stdout.strip()[:500]
        except (OSError, subprocess.SubprocessError):
            info["nvidia"] = "nvidia-smi failed"
    return info


def _add(archive: zipfile.ZipFile, path: Path, name: str) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES or path.suffix.casefold() in MEDIA_SUFFIXES:
            return False
        archive.write(path, name)
        return True
    except OSError:
        return False


def create_report(*, output_dir: Path | None, job_status: Path | None, database: Path | None,
                  destination: Path, now: float | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"DubFlow-report-{stamp}.zip"
    temporary = target.with_name(target.name + ".partial")
    summary: dict = {"schema_version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
                     "machine": _machine(output_dir), "included": []}
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if output_dir is not None and output_dir.is_dir():
            summary["output_dir"] = str(output_dir)
            for relative in REPORTS:
                if _add(archive, output_dir / relative, "job/" + relative):
                    summary["included"].append(relative)
            for path in sorted(output_dir.glob("editable/dubbing_spoken_text*.json")):
                if _add(archive, path, "job/editable/" + path.name):
                    summary["included"].append("editable/" + path.name)
            work = output_dir / ".dubflow-work" / "b2-audio"
            for path in sorted(work.rglob("*.json")) if work.is_dir() else ():
                if path.stem in WORK_DOCUMENTS and _add(archive, path, "job/b2-audio/" + path.relative_to(work).as_posix()):
                    summary["included"].append("b2-audio/" + path.relative_to(work).as_posix())
            rows, unique, listed = _sizes(output_dir)
            summary["disk"] = {"files": len(rows), "unique_bytes": unique, "listed_bytes": listed,
                               "longest_path": max((len(str(output_dir / row["path"])) for row in rows), default=0)}
            archive.writestr("file-sizes.json", json.dumps(rows, ensure_ascii=False, indent=1))
        for path, name in ((job_status, "job-status.json"), (database, "jobs.sqlite3")):
            if path is not None and _add(archive, path, name):
                summary["included"].append(name)
        archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    os.replace(temporary, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a DubFlow diagnostic report zip for one job.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--job-status", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    try:
        path = create_report(output_dir=args.output_dir, job_status=args.job_status, database=args.database,
                             destination=args.destination or _desktop())
        print(json.dumps({"ok": True, "path": str(path), "bytes": path.stat().st_size}, ensure_ascii=True))
        return 0
    except OSError as error:
        print(json.dumps({"ok": False, "message": f"Không tạo được báo cáo: {error}"}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

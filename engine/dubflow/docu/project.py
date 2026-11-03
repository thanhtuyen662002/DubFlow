"""The documentary project request (``spec.json``, ADR-0049) and its folder layout."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any
import unicodedata

SCHEMA_VERSION = 1
SPEC_LIMIT = 256 * 1024
FRAMING_PRESETS = ("none", "letterbox_235", "overscan_108")
MIN_SOURCES, MAX_SOURCES = 2, 5
MIN_MINUTES, MAX_MINUTES = 3, 20
TRANSCRIPT_SUFFIXES = frozenset({".json", ".srt"})
_SOURCE_ID = re.compile(r"src_\d{2}")


class DocuError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    video: Path
    transcript: Path
    origin_url: str | None
    credit: dict[str, Any] | None


@dataclass(frozen=True)
class Spec:
    title: str
    topic: str
    target_minutes: int
    cinematic_framing_preset: str
    burn_end_credits: bool
    sources: tuple[Source, ...]


def clean_text(value: Any, label: str, limit: int, *, required: bool = False, lines: bool = True) -> str:
    if type(value) is not str or len(value) > limit or (required and not value.strip()) or any(
            unicodedata.category(c) == "Cc" and not (lines and c in "\r\n\t") for c in value):
        raise DocuError(f"{label} không hợp lệ (tối đa {limit} ký tự)")
    return value.strip()


def _path(value: Any, label: str) -> Path:
    if type(value) is not str or not value or len(value) > 1024 or "\x00" in value:
        raise DocuError(f"{label} không hợp lệ")
    path = Path(value)
    if not path.is_absolute():
        raise DocuError(f"{label} phải là đường dẫn đầy đủ")
    return path


def load_spec(path: Path) -> Spec:
    try:
        if path.stat().st_size > SPEC_LIMIT:
            raise ValueError("too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocuError("Không đọc được yêu cầu phim tài liệu") from error
    fields = {"schema_version", "title", "topic", "target_minutes", "cinematic_framing_preset", "burn_end_credits", "sources"}
    if type(value) is not dict or set(value) != fields or value["schema_version"] != SCHEMA_VERSION \
            or type(value["schema_version"]) is not int:
        raise DocuError("Yêu cầu phim tài liệu không đúng phiên bản hoặc trường dữ liệu")
    minutes = value["target_minutes"]
    if type(minutes) is not int or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise DocuError(f"Độ dài mục tiêu phải từ {MIN_MINUTES} đến {MAX_MINUTES} phút")
    if value["cinematic_framing_preset"] not in FRAMING_PRESETS or type(value["burn_end_credits"]) is not bool:
        raise DocuError("Khung hình hoặc tùy chọn credit không hợp lệ")
    sources = value["sources"]
    if type(sources) is not list or not MIN_SOURCES <= len(sources) <= MAX_SOURCES:
        raise DocuError(f"Cần từ {MIN_SOURCES} đến {MAX_SOURCES} nguồn")
    parsed: list[Source] = []
    for index, item in enumerate(sources, 1):
        keys = {"id", "title", "video", "transcript", "origin_url", "credit"}
        if type(item) is not dict or set(item) != keys or item["id"] != f"src_{index:02d}" \
                or not _SOURCE_ID.fullmatch(str(item["id"])):
            raise DocuError(f"Nguồn {index} không hợp lệ")
        transcript = _path(item["transcript"], f"Lời gốc của nguồn {index}")
        if transcript.suffix.casefold() not in TRANSCRIPT_SUFFIXES:
            raise DocuError(f"Lời gốc của nguồn {index} phải là transcript.json hoặc .srt")
        url = item["origin_url"]
        if url is not None and (type(url) is not str or len(url) > 500 or not re.match(r"https?://\S+$", url)):
            raise DocuError(f"Link của nguồn {index} không hợp lệ")
        credit = item["credit"]
        if credit is not None and type(credit) is not dict:
            raise DocuError(f"Ghi nguồn của nguồn {index} không hợp lệ")
        parsed.append(Source(item["id"], clean_text(item["title"], f"Tên nguồn {index}", 120, required=True, lines=False),
                             _path(item["video"], f"Video của nguồn {index}"), transcript, url, credit))
    return Spec(clean_text(value["title"], "Tên phim", 120, required=True, lines=False),
                clean_text(value["topic"], "Chủ đề", 600), minutes, value["cinematic_framing_preset"],
                value["burn_end_credits"], tuple(parsed))


def work_dir(run_dir: Path) -> Path:
    return run_dir / ".dubflow-work"


def spec_path(run_dir: Path) -> Path:
    return work_dir(run_dir) / "spec.json"


def context_path(run_dir: Path, source_id: str) -> Path:
    return work_dir(run_dir) / "sources" / source_id / "source_context.json"


def script_path(run_dir: Path) -> Path:
    return run_dir / "documentary_script.json"


def write_json(path: Path, value: Any) -> None:
    """Atomic UTF-8 JSON publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)

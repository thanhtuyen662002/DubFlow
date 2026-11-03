"""One ``source_context.json`` per source: timed lines and their quantities (ADR-0049)."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Callable

from . import units
from .project import DocuError, Source, Spec, context_path, write_json

TRANSCRIPT_LIMIT = 32 * 1024 * 1024
SRT_LIMIT = 8 * 1024 * 1024
MAX_SEGMENTS = 20_000
MAX_TEXT = 2_000
_SRT_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_TAGS = re.compile(r"<[^>]*>|\{\\[^}]*\}")

Line = tuple[int, int, str]


def _ms(h: str, m: str, s: str, frac: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0"))


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding != "utf-16" or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return text
    raise DocuError("Không đọc được bảng mã của phụ đề")


def parse_srt(text: str) -> list[Line]:
    lines: list[Line] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "")):
        rows = [row.strip() for row in block.strip().splitlines()]
        for index, row in enumerate(rows):
            match = _SRT_TIME.search(row)
            if match:
                words = " ".join(_TAGS.sub("", value) for value in rows[index + 1:]).strip()
                start, end = _ms(*match.groups()[:4]), _ms(*match.groups()[4:])
                if words and end > start:
                    lines.append((start, end, words))
                break
    return lines


def parse_transcript(value: Any) -> list[Line]:
    """DubFlow's ``.dubflow-work/transcript.json``: cues with start_ms, end_ms and source_text."""
    cues = value.get("cues") if type(value) is dict else None
    if type(cues) is not list:
        raise DocuError("transcript.json không có danh sách câu")
    lines: list[Line] = []
    for cue in cues:
        if type(cue) is not dict:
            continue
        start, end, text = cue.get("start_ms"), cue.get("end_ms"), cue.get("source_text")
        if type(start) is int and type(end) is int and type(text) is str and 0 <= start < end and text.strip():
            lines.append((start, end, text.strip()))
    return lines


def read_lines(path: Path) -> list[Line]:
    limit = SRT_LIMIT if path.suffix.casefold() == ".srt" else TRANSCRIPT_LIMIT
    try:
        if path.stat().st_size > limit:
            raise DocuError(f"Lời gốc quá lớn: {path.name}")
        data = path.read_bytes()
    except OSError as error:
        raise DocuError(f"Không mở được lời gốc: {path.name}") from error
    if path.suffix.casefold() == ".srt":
        lines = parse_srt(_decode(data))
    else:
        try:
            lines = parse_transcript(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, ValueError) as error:
            raise DocuError(f"Không đọc được {path.name}") from error
    if not lines:
        raise DocuError(f"Lời gốc không có câu nào: {path.name}")
    return sorted(lines)[:MAX_SEGMENTS]


def build(source: Source, duration_ms: int) -> dict[str, Any]:
    if duration_ms <= 0:
        raise DocuError(f"Không đọc được thời lượng video: {source.video.name}")
    lines = [(start, min(end, duration_ms), text[:MAX_TEXT]) for start, end, text in read_lines(source.transcript)
             if start < duration_ms]
    width = max(3, len(str(len(lines))))
    segments = []
    for index, (start, end, text) in enumerate(lines, 1):
        quantities = [units.describe(quantity) for quantity in units.extract(text)
                      if quantity.dimension not in {"count", "year"} or quantity.value >= 10]
        segments.append({"seg_id": f"{source.id}_seg_{index:0{width}d}", "start": start / 1000, "end": end / 1000,
                         "original_text": text, "extracted_keywords": [item["text"] for item in quantities],
                         "quantities": quantities})
    return {"schema_version": 1, "source_id": source.id, "title": source.title, "video_path": str(source.video),
            "duration": duration_ms / 1000, "segments": segments}


def build_all(run_dir: Path, spec: Spec, probe: Callable[[Path], int]) -> list[dict[str, Any]]:
    """Write every source's context; the summary lists ids, durations and segment counts."""
    summary = []
    for source in spec.sources:
        if not source.video.is_file():
            raise DocuError(f"Không tìm thấy video nguồn: {source.video.name}")
        try:
            duration_ms = probe(source.video)
        except Exception as error:  # ffprobe adapters raise their own error types
            raise DocuError(f"Không đọc được thời lượng video: {source.video.name}") from error
        context = build(source, duration_ms)
        write_json(context_path(run_dir, source.id), context)
        summary.append({"id": source.id, "duration": context["duration"], "segments": len(context["segments"])})
    return summary

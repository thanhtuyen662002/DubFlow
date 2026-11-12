"""Tổng hợp from un-dubbed footage: the v2 programme spec (ADR-0053).

A programme has an optional hook line, an intro, ranked items and an outro.
Each item has its title, its new narration (or none, to be drafted by the
user's AI from the item's own notes and transcript), notes and things to
verify, and one or more raw sources: any local video the user chose, with an
optional trim, transcript (``.srt``), notes, credit and rights statement.

The ranking is the user's unless ``program.ranking`` is ``suggested``; a
suggestion is only applied when ``apply_suggestion`` is true. Nothing here
writes to a source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from engine.dubflow.product import render as narration_render
from engine.dubflow.product.job import ProductError, _file
from . import layout

CompilationError = ProductError
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"})
RIGHTS = ("owned", "licensed", "permitted", "unknown")
KINDS = ("top_n", "list")
KEEP_AUDIO = ("auto", "on", "off")
MAX_ITEMS = 30
MAX_SOURCES = 8
MAX_NARRATION = 1500


@dataclass(frozen=True)
class Credit:
    creator: str = ""
    platform: str = ""
    url: str = ""
    license: str = ""

    def line(self) -> str:
        author = f"{self.creator} ({self.platform})" if self.creator and self.platform else self.creator or self.platform
        parts = [part for part in (author, self.url, f"Giấy phép: {self.license}" if self.license else "") if part]
        return " – ".join(parts)


@dataclass(frozen=True)
class Source:
    video: Path
    start_ms: int | None = None
    end_ms: int | None = None
    transcript: Path | None = None
    notes: str = ""
    credit: Credit = field(default_factory=Credit)
    rights: str = "unknown"


@dataclass(frozen=True)
class Item:
    item_id: str
    rank: int | None
    title: str
    narration: str
    notes: str
    verify: tuple[str, ...]
    sources: tuple[Source, ...]
    keep_audio: str = "auto"


@dataclass(frozen=True)
class Program:
    title: str
    layout: str
    voice_id: str
    speed: float
    music: Path | None
    music_volume: float
    kind: str
    ranking: str
    apply_suggestion: bool
    hook: str
    intro: str
    outro: str
    items: tuple[Item, ...]
    mastering: Any = None
    editorial: Any = None


def _text(value: Any, label: str, limit: int, *, required: bool = False) -> str:
    from .job import text
    return text("" if value is None else value, label, limit, required=required)


def _whole(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise CompilationError(f"{label} phải là số mili giây nguyên không âm")
    return value


def _object(value: Any, label: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if type(value) is not dict or set(value) - allowed or not required <= set(value):
        raise CompilationError(f"{label}: thông tin không hợp lệ")
    return value


def _source(value: Any, label: str) -> Source:
    item = _object(value, label, {"video", "start_ms", "end_ms", "transcript", "notes", "credit", "rights"}, {"video"})
    video = _file(item["video"], VIDEO_SUFFIXES, "video nguồn")
    start, end = _whole(item.get("start_ms"), "Mốc bắt đầu"), _whole(item.get("end_ms"), "Mốc kết thúc")
    if end is not None and end <= (start or 0):
        raise CompilationError(f"{label}: mốc kết thúc phải sau mốc bắt đầu")
    transcript = None
    if item.get("transcript") is not None:
        transcript = _file(item["transcript"], frozenset({".srt"}), "phụ đề nguồn")
    else:
        beside = video.parent / "captions_vi.srt"  # a Hàng đợi run keeps its Vietnamese subtitles beside the source
        transcript = beside if beside.is_file() else None
    credit_value = item.get("credit")
    credit = Credit()
    if credit_value is not None:
        c = _object(credit_value, f"{label}: nguồn", {"creator", "platform", "url", "license"})
        credit = Credit(*(_text(c.get(k), "Thông tin nguồn", n) for k, n in (("creator", 120), ("platform", 40), ("url", 500),
                                                                             ("license", 80))))
    else:
        credit = _queue_credit(video)
    rights = item.get("rights", "unknown")
    if rights not in RIGHTS:
        raise CompilationError(f"{label}: quyền sử dụng không hợp lệ")
    return Source(video, start, end, transcript, _text(item.get("notes"), "Ghi chú nguồn", 400), credit, rights)


def _queue_credit(video: Path) -> Credit:
    """The credit a Hàng đợi run wrote next to its source (``source_credit.json``), when there is one."""
    path = video.parent / "source_credit.json"
    try:
        if not path.is_file() or path.stat().st_size > 16 * 1024:
            return Credit()
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict or value.get("schema_version") != 1:
            return Credit()
        return Credit(*(" ".join(str(value.get(k) or "").split())[:n] for k, n in (("creator", 120), ("platform", 40), ("url", 500))))
    except (OSError, ValueError):
        return Credit()


def load(value: dict) -> Program:
    """A v2 spec (already JSON) as a Program; raises CompilationError with a Vietnamese reason."""
    allowed = {"schema_version", "title", "layout", "voice_id", "speed", "music", "music_volume", "program", "hook", "intro",
               "outro", "items", "mastering", "editorial"}
    _object(value, "Yêu cầu tổng hợp", allowed, {"schema_version", "title", "layout", "voice_id", "items"})
    if value["layout"] not in layout.LAYOUTS:
        raise CompilationError("Bố cục không hợp lệ")
    speed, volume = value.get("speed", 1.0), value.get("music_volume", 0.12)
    for number, low, high in ((speed, 1, 1.3), (volume, 0, 1)):
        if type(number) not in (int, float) or not low <= number <= high:
            raise CompilationError("Tốc độ đọc hoặc âm lượng không hợp lệ")
    program = _object(value.get("program", {}), "Chương trình", {"kind", "ranking", "apply_suggestion"})
    kind, ranking = program.get("kind", "top_n"), program.get("ranking", "user")
    if kind not in KINDS or ranking not in ("user", "suggested") or type(program.get("apply_suggestion", False)) is not bool:
        raise CompilationError("Kiểu chương trình hoặc cách xếp hạng không hợp lệ")
    items_value = value["items"]
    if type(items_value) is not list or not 2 <= len(items_value) <= MAX_ITEMS:
        raise CompilationError(f"Cần từ 2 đến {MAX_ITEMS} mục")
    items, ids = [], set()
    for number, raw in enumerate(items_value, 1):
        label = f"Mục {number}"
        item = _object(raw, label, {"item_id", "rank", "title", "narration", "notes", "verify", "sources", "keep_source_audio"},
                       {"title", "sources"})
        item_id = item.get("item_id") or f"i{number}"
        if type(item_id) is not str or not item_id.isascii() or not item_id.replace("-", "").replace("_", "").isalnum() \
                or len(item_id) > 40 or item_id in ids:
            raise CompilationError(f"{label}: mã mục không hợp lệ hoặc bị trùng")
        ids.add(item_id)
        rank = item.get("rank")
        if rank is not None and (type(rank) is not int or not 1 <= rank <= 1000):
            raise CompilationError(f"{label}: thứ hạng phải là số nguyên dương")
        sources = item["sources"]
        if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
            raise CompilationError(f"{label}: cần từ 1 đến {MAX_SOURCES} video nguồn")
        verify = item.get("verify", [])
        if type(verify) is not list or len(verify) > 10:
            raise CompilationError(f"{label}: ghi chú kiểm chứng không hợp lệ")
        keep = item.get("keep_source_audio", "auto")
        if keep not in KEEP_AUDIO:
            raise CompilationError(f"{label}: cách giữ âm thanh nguồn không hợp lệ")
        try:
            items.append(Item(item_id, rank, _text(item["title"], "Tiêu đề mục", 80, required=True),
                              _text(item.get("narration"), "Lời bình", MAX_NARRATION), _text(item.get("notes"), "Ghi chú", 600),
                              tuple(_text(v, "Ghi chú kiểm chứng", 200, required=True) for v in verify),
                              tuple(_source(s, f"{label}, nguồn {i}") for i, s in enumerate(sources, 1)), keep))
        except CompilationError as error:
            raise CompilationError(f"{label}: {error.message}") from error
    ranks = [item.rank for item in items if item.rank is not None]
    if len(ranks) != len(set(ranks)):
        raise CompilationError("Có hai mục cùng thứ hạng")
    mastering = None
    if value.get("mastering") is not None:
        from engine.dubflow.mastering import plan as mastering_plan
        try:
            mastering = mastering_plan.Options.from_dict(value["mastering"])
        except mastering_plan.PlanError as error:
            raise CompilationError("Tùy chọn tối ưu hậu kỳ không hợp lệ") from error
    from engine.dubflow.mastering import plan as mastering_plan
    if mastering is None:
        mastering = mastering_plan.Options("balanced", "entertainment")  # the raw path is always planned (ADR-0053 §5)
    elif not mastering.enabled:
        mastering = mastering_plan.Options("light", mastering.style)  # "Tắt": the fewest operations, still a plan
    editorial = _editorial(value.get("editorial"))
    return Program(_text(value["title"], "Tiêu đề", 120, required=True), value["layout"],
                   _text(value["voice_id"], "Giọng đọc", 96, required=True), float(speed),
                   None if value.get("music") is None else _file(value["music"], narration_render.MUSIC_SUFFIXES, "nhạc nền"),
                   float(volume), kind, ranking, bool(program.get("apply_suggestion", False)),
                   _text(value.get("hook"), "Câu mở màn", 300), _text(value.get("intro"), "Mở đầu", 800),
                   _text(value.get("outro"), "Kết thúc", 800), tuple(items), mastering, editorial)


@dataclass(frozen=True)
class Editorial:
    edits: dict[str, Any] = field(default_factory=dict)   # part id → align.Edits
    ai: dict[str, str] | None = None
    vision_budget: int = 80
    draft_narration: bool = True
    budget_set: bool = False   # the spec gave vision_budget: kept as given; otherwise it follows the length (P2-N11)

    def digest_input(self, part: str) -> dict[str, Any]:
        edits = self.edits.get(part)
        return {"edits": None if edits is None else edits.to_dict(), "ai": self.ai, "vision_budget": self.vision_budget}


def _editorial(value: Any) -> Editorial:
    if value is None:
        return Editorial()
    from engine.dubflow.mastering import align
    item = _object(value, "Thông tin biên tập", {"edits", "ai", "vision_budget", "draft_narration"})
    edits = {}
    raw = item.get("edits", {})
    if type(raw) is not dict or len(raw) > 64:
        raise CompilationError("Chỉnh sửa lựa chọn cảnh không hợp lệ")
    for part, change in raw.items():
        try:
            edits[str(part)] = align.Edits.from_dict(change)
        except ValueError as error:
            raise CompilationError("Chỉnh sửa lựa chọn cảnh không hợp lệ") from error
    ai = item.get("ai")
    if ai is not None:
        ai = _object(ai, "Cài đặt AI", {"provider", "model", "base_url"}, {"provider"})
        if any(v is not None and (type(v) is not str or len(v) > 300) for v in ai.values()):
            raise CompilationError("Cài đặt AI không hợp lệ")
        ai = {k: v for k, v in ai.items() if v}
    budget = item.get("vision_budget", 80)
    if type(budget) is not int or not 0 <= budget <= 400 or type(item.get("draft_narration", True)) is not bool:
        raise CompilationError("Thông tin biên tập không hợp lệ")
    return Editorial(edits, ai, budget, item.get("draft_narration", True), "vision_budget" in item)


__all__ = ["Credit", "Editorial", "Item", "Program", "Source", "load"]

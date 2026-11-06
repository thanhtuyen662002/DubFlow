"""Natural, in-context Vietnamese subtitles from the user's own AI key.

The offline translators (Argos, Hachimi) see one cue at a time, so dialogue
comes out word for word: no context, wrong pronouns, idioms taken literally.
When the user has saved an AI key, this pass hands the AI the whole
conversation instead:

1. one short brief of the whole video (topic, who speaks to whom and how they
   address each other, names and terms), from the source lines;
2. windows of consecutive cues, each with the lines just before it (already in
   Vietnamese), the brief and running notes, and the offline draft as a hint.

Every cue keeps its id and timing. A cue the AI skipped, or a window that
failed, keeps its offline translation, so a broken key or network never stops
the video (AGENTS.md: live services cannot be required for a usable export).
Windows are cached by their exact inputs, so a resumed job does not pay twice.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from engine.dubflow.ai.client import AiError, Route, ask

RECIPE = "context-ai-vi-v1"
WINDOW_CUES = 40
CONTEXT_CUES = 6
BRIEF_SOURCE_CHARS = 12_000
MAX_NOTES_CHARS = 1_200
# Syllables per second a Vietnamese voice says comfortably; subtitles alone may be denser.
DUB_SYLLABLES_PER_SECOND = 4.5
READ_SYLLABLES_PER_SECOND = 6.0
WARNING = "AI_TRANSLATION_PARTIAL: "


class Cue(Protocol):
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None


Asker = Callable[..., tuple[Any, Route]]


@dataclass(frozen=True)
class ContextResult:
    cues: tuple[Any, ...]
    windows_total: int
    windows_done: int
    cues_from_ai: int
    model: str
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.windows_done == self.windows_total


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "tom_tat": {"type": "string"},
        "the_loai": {"type": "string"},
        "nhan_vat": {"type": "array", "items": {"type": "object", "properties": {
            "ten": {"type": "string"}, "vai": {"type": "string"}, "xung_ho": {"type": "string"}},
            "required": ["ten", "vai", "xung_ho"], "additionalProperties": False}},
        "thuat_ngu": {"type": "array", "items": {"type": "object", "properties": {
            "goc": {"type": "string"}, "viet": {"type": "string"}}, "required": ["goc", "viet"], "additionalProperties": False}},
    },
    "required": ["tom_tat", "the_loai", "nhan_vat", "thuat_ngu"],
    "additionalProperties": False,
}

LINES_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                                             "required": ["id", "text"], "additionalProperties": False}},
        "ghi_chu": {"type": "string"},
    },
    "required": ["lines", "ghi_chu"],
    "additionalProperties": False,
}

BRIEF_SYSTEM = """Bạn là biên tập viên phụ đề. Bạn nhận toàn bộ lời thoại gốc (thường là tiếng Trung, do máy nhận dạng giọng nói nên có thể sai chữ đồng âm) của một video.
Hãy đọc hết rồi tóm tắt bằng tiếng Việt có dấu để người dịch hiểu ngữ cảnh:
- tom_tat: video nói về gì, mạch diễn biến, giọng điệu (2-4 câu).
- the_loai: ví dụ phim ngắn tình cảm, hài, review sản phẩm, phóng sự, vlog, hướng dẫn, kể chuyện.
- nhan_vat: người nói/nhân vật, vai trò, quan hệ và cách xưng hô tiếng Việt phù hợp (ví dụ "gọi mẹ là mẹ, xưng con"; "hai người yêu nhau: anh - em"; "người dẫn nói với khán giả: mình - các bạn").
- thuat_ngu: tên riêng, thương hiệu, thuật ngữ và cách viết tiếng Việt thống nhất (tên người Trung dùng âm Hán Việt, ví dụ 李明 → Lý Minh).
Chỉ dựa vào lời thoại; không bịa."""

LINES_SYSTEM = """Bạn là biên dịch viên phụ đề video chuyên nghiệp, Việt hóa lời thoại sang tiếng Việt tự nhiên như người Việt nói.
Bạn nhận một đoạn liên tiếp các dòng phụ đề: lời gốc (thường là tiếng Trung, do máy nhận dạng giọng nói nên có thể sai chữ đồng âm), bản dịch máy (chỉ để tham khảo, thường dịch sát từng chữ và hay sai), thời lượng và số tiếng tối đa.
Quy tắc:
- Đọc cả đoạn (và các câu trước đó) để hiểu ý, rồi dịch theo Ý và NGỮ CẢNH, không dịch từng chữ. Câu phải trôi chảy, đúng văn nói tiếng Việt, đúng giọng điệu (hài thì dí dỏm, cảm động thì nhẹ nhàng, review thì rõ ràng).
- Một câu nói có thể bị cắt ngang qua nhiều dòng: hiểu theo cả câu, nhưng mỗi id chỉ chứa phần ý của dòng đó, theo đúng thứ tự, để phụ đề khớp hình. Không dồn ý sang dòng khác, không để trống dòng nào.
- Xưng hô theo quan hệ giữa người nói và người nghe (anh/em, con/mẹ, mày/tao, tôi/anh, mình/các bạn...) và giữ nhất quán suốt video.
- Thành ngữ, tục ngữ, tiếng lóng, câu đùa: dùng cách nói tương đương của người Việt, không dịch nghĩa đen.
- Tên người Trung viết theo âm Hán Việt; thương hiệu, tên tiếng Anh giữ nguyên; thống nhất theo phần ghi chú.
- Nếu lời gốc rõ ràng bị nhận dạng sai, suy ra ý đúng theo ngữ cảnh.
- Mỗi dòng KHÔNG vượt quá số tiếng tối đa (một tiếng = một âm tiết tiếng Việt) để đọc và lồng tiếng vừa thời lượng; ưu tiên câu gọn, bỏ từ đệm thừa.
- Giữ nguyên số liệu, giá tiền, đơn vị. Không thêm thông tin, không giải thích, không ghi chú trong ngoặc, không emoji.
- Viết tiếng Việt có dấu đầy đủ.
- Trả đủ mọi id đã cho. ghi_chu: cập nhật ngắn gọn (dưới 600 ký tự) cách xưng hô, tên riêng, thuật ngữ đã dùng để các đoạn sau dịch thống nhất."""


def syllable_budget(cue: Cue, *, dubbing: bool) -> int:
    seconds = max(0.0, (cue.end_ms - cue.start_ms) / 1000)
    return max(3, int(seconds * (DUB_SYLLABLES_PER_SECOND if dubbing else READ_SYLLABLES_PER_SECOND)))


def _syllables(text: str) -> int:
    return len([word for word in text.split() if re.search(r"\w", word)])


_MARKED = re.compile(r"[À-ÃÈ-ÊÌÍÒ-ÕÙÚÝà-ãè-êìíò-õùúýĂăĐđĨĩŨũƠơƯưẠ-ỹ]")


def lacks_diacritics(texts: Sequence[str]) -> bool:
    """Vietnamese written without its marks ("Ban co bao gio..."): 40+ letters, almost none marked."""
    text = " ".join(texts)
    letters = len(re.findall(r"[^\W\d_]", text))
    return letters >= 40 and len(_MARKED.findall(text)) / letters < 0.03


def _clean(text: str) -> str:
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", text)
    text = re.sub(r"[*_`#]", "", text)
    return " ".join(text.split()).strip().strip('"“”')


def _brief_text(brief: Any) -> str:
    if not isinstance(brief, dict):
        return ""
    lines = []
    if isinstance(brief.get("tom_tat"), str) and brief["tom_tat"].strip():
        lines.append("Tóm tắt: " + brief["tom_tat"].strip()[:800])
    if isinstance(brief.get("the_loai"), str) and brief["the_loai"].strip():
        lines.append("Thể loại: " + brief["the_loai"].strip()[:120])
    for person in (brief.get("nhan_vat") or [])[:12]:
        if isinstance(person, dict):
            lines.append(f"Nhân vật: {str(person.get('ten', ''))[:60]} — {str(person.get('vai', ''))[:120]}; xưng hô: {str(person.get('xung_ho', ''))[:160]}")
    terms = [f"{str(item.get('goc', ''))[:40]} → {str(item.get('viet', ''))[:60]}" for item in (brief.get("thuat_ngu") or [])[:40]
             if isinstance(item, dict) and item.get("goc") and item.get("viet")]
    if terms:
        lines.append("Thuật ngữ: " + "; ".join(terms))
    return "\n".join(lines)[:3000]


def _window_prompt(window: Sequence[Cue], before: Sequence[tuple[Cue, str]], brief: str, notes: str, *, dubbing: bool,
                   title: str, part: int, parts: int) -> str:
    rows = [json.dumps({"id": cue.cue_id, "giay": round((cue.end_ms - cue.start_ms) / 1000, 1), "toi_da_tieng": syllable_budget(cue, dubbing=dubbing),
                        "goc": cue.source_text[:400], "dich_may": (cue.translated_text or "")[:400]}, ensure_ascii=False) for cue in window]
    previous = [json.dumps({"goc": cue.source_text[:300], "da_dich": text[:300]}, ensure_ascii=False) for cue, text in before]
    return "\n".join(filter(None, [
        f"Tên video: {title[:200]}" if title else "",
        f"Bối cảnh cả video:\n{brief}" if brief else "",
        f"Ghi chú từ các đoạn trước:\n{notes}" if notes else "",
        f"Đây là đoạn {part}/{parts} của video." if parts > 1 else "",
        "Các câu ngay trước (đã dịch, chỉ để nối mạch, KHÔNG dịch lại):\n" + "\n".join(previous) if previous else "",
        "Các dòng cần dịch (mỗi dòng một JSON):",
        *rows,
    ]))


def _lines(value: Any, window: Sequence[Cue], *, dubbing: bool) -> dict[str, str]:
    """The usable lines of an answer, by cue id: known ids, real text, not wildly over the budget."""
    known = {cue.cue_id: cue for cue in window}
    result: dict[str, str] = {}
    items = value.get("lines") if isinstance(value, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        cue_id, text = item.get("id"), item.get("text")
        if not isinstance(cue_id, str) or cue_id not in known or cue_id in result or not isinstance(text, str):
            continue
        text = _clean(text)
        if not re.search(r"\w", text) or len(text) > 1000:
            continue
        # Twice the budget means the model merged or invented content: keep the draft instead.
        if _syllables(text) > 2 * syllable_budget(known[cue_id], dubbing=dubbing) + 4:
            continue
        result[cue_id] = text
    return result


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cached(path: Path | None) -> Any:
    if path is None or not path.is_file():
        return None
    try:
        if path.stat().st_size > 1_048_576:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _store(path: Path | None, value: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def identity(route: Route, *, dubbing: bool) -> dict[str, Any]:
    """What changes the AI pass's output: never the key."""
    return {"recipe": RECIPE, "provider": route.provider, "model": route.model, "base_url": route.base_url, "dubbing": dubbing}


def translate_in_context(cues: Sequence[Any], route: Route, *, dubbing: bool, cache_dir: Path | None = None, title: str = "",
                         on_progress: Callable[[int, int], None] | None = None, asker: Asker = ask) -> ContextResult:
    """Every cue with an in-context translation where the AI gave one, otherwise its offline draft."""
    ordered = list(cues)
    windows = [ordered[index:index + WINDOW_CUES] for index in range(0, len(ordered), WINDOW_CUES)]
    if not windows:
        return ContextResult((), 0, 0, 0, route.model)
    key = identity(route, dubbing=dubbing)
    current = route
    error: str | None = None

    # 1. The brief. Optional: a failure here only loses some context.
    sources = "\n".join(cue.source_text for cue in ordered)[:BRIEF_SOURCE_CHARS]
    brief_path = cache_dir / f"brief-{_digest({'k': key, 's': sources, 't': title})}.json" if cache_dir else None
    brief_value = _cached(brief_path)
    if brief_value is None:
        try:
            brief_value, current = asker(current, BRIEF_SYSTEM, BRIEF_SCHEMA, [{"text": (f"Tên video: {title}\n" if title else "") + "Lời thoại gốc:\n" + sources}],
                                         max_tokens=4000)
            _store(brief_path, brief_value)
        except AiError as failure:
            if failure.code in {"KEY", "BALANCE", "MODEL", "INVALID"}:
                # Nothing else will work either: keep every offline line.
                return ContextResult(tuple(ordered), len(windows), 0, 0, route.model, failure.message)
            brief_value, error = None, failure.message
    brief = _brief_text(brief_value)

    # 2. The windows, in order, each continuing the previous one's notes.
    notes = ""
    done = from_ai = 0
    finished: dict[str, str] = {}
    for number, window in enumerate(windows, start=1):
        if on_progress:
            on_progress(number - 1, len(windows))
        start = ordered.index(window[0])
        before = [(cue, finished.get(cue.cue_id, cue.translated_text or "")) for cue in ordered[max(0, start - CONTEXT_CUES):start]]
        prompt = _window_prompt(window, before, brief, notes, dubbing=dubbing, title=title, part=number, parts=len(windows))
        path = cache_dir / f"window-{_digest({'k': key, 'p': prompt})}.json" if cache_dir else None
        value = _cached(path)
        if value is None:
            try:
                value, current = asker(current, LINES_SYSTEM, LINES_SCHEMA, [{"text": prompt}], max_tokens=12000)
                lines = _lines(value, window, dubbing=dubbing)
                if lines and lacks_diacritics(list(lines.values())):
                    value, current = asker(current, LINES_SYSTEM, LINES_SCHEMA, [{"text": prompt}], max_tokens=12000)
                lines = _lines(value, window, dubbing=dubbing)
                if lines and not lacks_diacritics(list(lines.values())):
                    _store(path, value)
            except AiError as failure:
                error = failure.message
                if failure.code in {"KEY", "BALANCE", "MODEL", "INVALID"}:
                    break
                continue
        lines = _lines(value, window, dubbing=dubbing)
        if not lines or lacks_diacritics(list(lines.values())):
            error = error or "AI không trả về bản dịch dùng được cho một đoạn."
            continue
        done += 1
        from_ai += len(lines)
        finished.update(lines)
        if isinstance(value, dict) and isinstance(value.get("ghi_chu"), str):
            notes = value["ghi_chu"].strip()[:MAX_NOTES_CHARS]
    if on_progress:
        on_progress(len(windows), len(windows))
    result = tuple(replace(cue, translated_text=finished[cue.cue_id]) if cue.cue_id in finished else cue for cue in ordered)
    return ContextResult(result, len(windows), done, from_ai, current.model, None if done == len(windows) else error)

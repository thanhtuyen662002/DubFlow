"""Natural, in-context Vietnamese subtitles from the user's own AI key.

The offline translators (Argos, Hachimi) see one cue at a time, so dialogue
comes out word for word: no context, wrong pronouns, idioms taken literally.
When the user has saved an AI key, this pass hands the AI the whole
conversation instead:

1. one short brief of the whole video (topic, who speaks to whom and how they
   address each other, names and terms), from the source lines;
2. windows of consecutive cues, each with the lines just before it (already in
   Vietnamese), the brief and running notes, and the offline draft as a hint;
3. one pass over the lines that still call a person by another third-person
   word than the brief fixed for them ("anh ta" in one line, "gã" in the next).

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

RECIPE = "context-ai-vi-v2"
WINDOW_CUES = 40
CONTEXT_CUES = 6
BRIEF_SOURCE_CHARS = 12_000
MAX_NOTES_CHARS = 1_200
# Syllables per second a Vietnamese voice says comfortably; subtitles alone may be denser.
DUB_SYLLABLES_PER_SECOND = 4.5
READ_SYLLABLES_PER_SECOND = 6.0
WARNING = "AI_TRANSLATION_PARTIAL: "
# Failures no retry can fix: the key, the balance, the model or the request itself.
FATAL_CODES = frozenset({"KEY", "BALANCE", "MODEL", "INVALID"})


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
    # Lines whose third-person words could not be checked (the consistency pass failed for them).
    unify_pending: int = 0
    unify_error: str | None = None

    @property
    def complete(self) -> bool:
        return self.windows_done == self.windows_total


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "tom_tat": {"type": "string"},
        "the_loai": {"type": "string"},
        "nhan_vat": {"type": "array", "items": {"type": "object", "properties": {
            "ten": {"type": "string"}, "vai": {"type": "string"}, "xung_ho": {"type": "string"}, "goi_ngoi_ba": {"type": "string"}},
            "required": ["ten", "vai", "xung_ho", "goi_ngoi_ba"], "additionalProperties": False}},
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
  goi_ngoi_ba: MỘT cách gọi duy nhất khi người kể hoặc người khác nhắc tới nhân vật đó ở ngôi thứ ba, dùng suốt video (ví dụ "anh ta", "cô ấy", "ông lão", "hắn" cho kẻ xấu); để trống nếu không ai nhắc tới người đó ở ngôi thứ ba.
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
- KHÔNG bỏ ý: mỗi dòng phải giữ đủ thông tin của lời gốc dòng đó (ai, làm gì, vì sao, cảm xúc). Câu tự nhiên nhưng không được lược mất nội dung.
- Giữ số tiếng của mỗi dòng trong khoảng tối đa đã cho (một tiếng = một âm tiết tiếng Việt) để đọc và lồng tiếng vừa thời lượng: ưu tiên câu gọn, bỏ từ đệm thừa; chỉ khi bắt buộc để giữ đủ ý mới được vượt, tối đa thêm 20%.
- Ngôi thứ ba: nhắc tới nhân vật nào thì dùng ĐÚNG cách gọi "ngôi thứ ba" của nhân vật đó trong bối cảnh, ở mọi dòng; không đổi sang "gã", "hắn", "y", "anh ấy"... cho đa dạng.
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


# Words that refer to a person in the third person. One person called "anh ta" in one line and "gã" in
# the next reads as two people; the brief fixes one per person and the last pass enforces it.
THIRD_PERSON = ("anh ta", "anh ấy", "cô ta", "cô ấy", "chị ta", "chị ấy", "cậu ta", "cậu ấy", "ông ta", "ông ấy",
                "bà ta", "bà ấy", "hắn", "gã", "ả")
UNIFY_BATCH = 60

UNIFY_SYSTEM = """Bạn là biên tập viên phụ đề tiếng Việt. Một video gọi cùng một nhân vật ở ngôi thứ ba bằng nhiều cách khác nhau (lúc "anh ta", lúc "gã"), khiến người xem tưởng là nhiều người.
Bạn nhận danh sách nhân vật với cách gọi ngôi thứ ba đã chọn, và các dòng phụ đề cần soát (lời gốc và bản dịch hiện tại).
Quy tắc:
- Chỉ thay từ gọi ngôi thứ ba cho đúng cách gọi đã chọn của nhân vật mà dòng đó nói tới. Giữ nguyên mọi chữ khác, giữ nguyên ý.
- Nếu từ đó chỉ một người không có trong danh sách, hoặc là lời thoại của nhân vật (mắng, gọi nhau) và cách gọi đó hợp ngữ cảnh, giữ nguyên dòng.
- Viết tiếng Việt có dấu đầy đủ. Trả lại đủ mọi id đã cho; ghi_chu để trống."""


def _third_person(person: Any) -> str:
    value = person.get("goi_ngoi_ba") if isinstance(person, dict) else None
    return " ".join(value.split())[:40] if isinstance(value, str) else ""


def _word(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)


_THIRD_PERSON_WORDS = {phrase: _word(phrase) for phrase in THIRD_PERSON}


# A bare "anh" for the person the brief calls "anh ta" ("Lúc này anh mới nhận ra…" next to "Anh ta sắp
# phát điên…") reads as a second man; only counted when "anh ta"/"anh ấy" was chosen.
_BARE_ANH = re.compile(r"(?<!\w)anh(?!\s+(?:ta|ấy)(?!\w))(?!\w)", re.IGNORECASE)


def stray_references(text: str, chosen: set[str]) -> list[str]:
    """The third-person words in a line that the brief did not choose for anyone."""
    stray = [phrase for phrase, pattern in _THIRD_PERSON_WORDS.items() if phrase not in chosen and pattern.search(text)]
    if chosen & {"anh ta", "anh ấy"} and _BARE_ANH.search(text):
        stray.append("anh")
    return stray


def needs_unify(text: str, chosen: set[str]) -> bool:
    """Whether a line has to be checked: with one chosen word, only a different word is a drift; with
    several people, any third-person word may name the wrong one ("cô ấy" for the man), so all are checked."""
    if len(chosen) > 1:
        return any(pattern.search(text) for pattern in _THIRD_PERSON_WORDS.values())
    return bool(stray_references(text, chosen))


@dataclass(frozen=True)
class _Unified:
    lines: dict[str, str]
    route: Route
    pending: int = 0
    error: str | None = None


def _unify(asker: Asker, route: Route, ordered: Sequence[Cue], finished: dict[str, str], brief_value: Any, *,
           dubbing: bool, cache_dir: Path | None, key: dict[str, Any]) -> _Unified:
    """Lines whose third-person words may not match the brief's choice, checked in batches and rewritten to it.

    Best effort: a batch that fails keeps its lines as translated and is counted in ``pending``."""
    people = [person for person in (brief_value.get("nhan_vat") or [])[:12] if isinstance(person, dict)] if isinstance(brief_value, dict) else []
    chosen = {_third_person(person).lower() for person in people} - {""}
    if not chosen:
        return _Unified({}, route)
    flagged = [cue for cue in ordered if cue.cue_id in finished and needs_unify(finished[cue.cue_id], chosen)]
    cast = [f"- {str(person.get('ten', ''))[:60]} ({str(person.get('vai', ''))[:80]}): {_third_person(person)}" for person in people if _third_person(person)]
    fixed: dict[str, str] = {}
    pending = 0
    error: str | None = None
    for index in range(0, len(flagged), UNIFY_BATCH):
        batch = flagged[index:index + UNIFY_BATCH]
        rows = [json.dumps({"id": cue.cue_id, "toi_da_tieng": syllable_budget(cue, dubbing=dubbing), "goc": cue.source_text[:300],
                            "hien_tai": finished[cue.cue_id]}, ensure_ascii=False) for cue in batch]
        prompt = "\n".join(["Nhân vật và cách gọi ngôi thứ ba đã chọn:", *cast, "Các dòng cần soát (mỗi dòng một JSON):", *rows])
        path = cache_dir / f"unify-{_digest({'k': key, 'p': prompt})}.json" if cache_dir else None
        value = _cached(path)
        if value is None:
            try:
                value, route = asker(route, UNIFY_SYSTEM, LINES_SCHEMA, [{"text": prompt}], max_tokens=8000)
            except AiError as failure:
                pending, error = pending + len(batch), failure.message
                continue
        lines = _lines(value, batch, dubbing=dubbing)
        if not lines or lacks_diacritics(list(lines.values())):
            pending, error = pending + len(batch), error or "AI không trả về bản soát xưng hô dùng được."
            continue
        _store(path, value)
        fixed.update(lines)
    return _Unified(fixed, route, pending, error)


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
            third = _third_person(person)
            lines.append(f"Nhân vật: {str(person.get('ten', ''))[:60]} — {str(person.get('vai', ''))[:120]}; xưng hô: {str(person.get('xung_ho', ''))[:160]}"
                         + (f"; ngôi thứ ba luôn gọi: {third}" if third else ""))
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


def _ask_missing(asker: Asker, route: Route, value: Any, window: Sequence[Cue], missing: Sequence[Cue], lines: dict[str, str],
                 brief: str, notes: str, *, dubbing: bool, title: str) -> Any:
    """The window's answer with the missing lines filled in, when a second request gives them."""
    before = [(cue, lines[cue.cue_id]) for cue in window if cue.cue_id in lines][-CONTEXT_CUES:]
    prompt = _window_prompt(missing, before, brief, notes, dubbing=dubbing, title=title, part=1, parts=1)
    try:
        extra, _ = asker(route, LINES_SYSTEM, LINES_SCHEMA, [{"text": prompt}], max_tokens=4000)
    except AiError:
        return value
    found = _lines(extra, missing, dubbing=dubbing)
    if not found or lacks_diacritics(list(found.values())):
        return value
    merged = dict(value) if isinstance(value, dict) else {"ghi_chu": ""}
    merged["lines"] = [{"id": key, "text": text} for key, text in lines.items()] + [{"id": key, "text": text} for key, text in found.items()]
    return merged


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
            if failure.code in FATAL_CODES:
                # Nothing else will work either: keep every offline line.
                return ContextResult(tuple(ordered), len(windows), 0, 0, route.model, failure.message)
            brief_value, error = None, failure.message
    brief = _brief_text(brief_value)

    # 2. The windows, in order, each continuing the previous one's notes. A window that failed on a
    # passing problem (network cut, server busy, time-out) is tried once more after the others, so a
    # short outage in a two-hour video does not leave a hole of offline lines.
    notes_after: dict[int, str] = {}
    done = from_ai = 0
    finished: dict[str, str] = {}
    fatal = False

    def run(number: int) -> bool:
        nonlocal current, error, done, from_ai, fatal
        window = windows[number - 1]
        start = (number - 1) * WINDOW_CUES
        before = [(cue, finished.get(cue.cue_id, cue.translated_text or "")) for cue in ordered[max(0, start - CONTEXT_CUES):start]]
        notes = next((notes_after[earlier] for earlier in range(number - 1, 0, -1) if earlier in notes_after), "")
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
                missing = [cue for cue in window if cue.cue_id not in lines]
                if lines and missing and not lacks_diacritics(list(lines.values())):
                    # Lines the model skipped or overran: asked once more, with the window's answer as context.
                    value = _ask_missing(asker, current, value, window, missing, lines, brief, notes, dubbing=dubbing, title=title)
                    lines = _lines(value, window, dubbing=dubbing)
                if lines and not lacks_diacritics(list(lines.values())):
                    _store(path, value)
            except AiError as failure:
                error = failure.message
                fatal = failure.code in FATAL_CODES
                return False
        lines = _lines(value, window, dubbing=dubbing)
        if not lines or lacks_diacritics(list(lines.values())):
            error = error or "AI không trả về bản dịch dùng được cho một đoạn."
            return False
        done += 1
        from_ai += len(lines)
        finished.update(lines)
        notes_after[number] = value["ghi_chu"].strip()[:MAX_NOTES_CHARS] if isinstance(value, dict) and isinstance(value.get("ghi_chu"), str) else ""
        return True

    failed: list[int] = []
    for number in range(1, len(windows) + 1):
        if on_progress:
            on_progress(number - 1, len(windows))
        if not run(number):
            failed.append(number)
            if fatal:
                break
    if failed and not fatal:
        for number in failed:
            if not run(number) and fatal:
                break
    # 3. One name per person: the lines that drifted ("anh ta" … "gã") brought back to the brief's choice.
    unified = _Unified({}, current)
    if finished:
        unified = _unify(asker, current, ordered, finished, brief_value, dubbing=dubbing, cache_dir=cache_dir, key=key)
        current = unified.route
        finished.update(unified.lines)
    if on_progress:
        on_progress(len(windows), len(windows))
    result = tuple(replace(cue, translated_text=finished[cue.cue_id]) if cue.cue_id in finished else cue for cue in ordered)
    return ContextResult(result, len(windows), done, from_ai, current.model, None if done == len(windows) else error,
                         unify_pending=unified.pending, unify_error=unified.error)

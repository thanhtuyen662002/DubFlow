"""What is known about each candidate's content, where it came from and how sure it is (ADR-0054 §2).

Evidence comes from the user (their notes on a clip: the strongest), the file's
name, a transcript (what is said inside the span), the optional vision adapter
(what a multimodal model saw on the keyframe) and the measured signals. Every
item keeps its origin, so a decision can say *why* a shot was chosen and the
desktop can show it. Labels come from a closed vocabulary and are only those the
evidence states; nothing is inferred from motion.

Text is folded for matching: lower case, Vietnamese diacritics removed (đ → d),
punctuation dropped, common function words skipped; terms are single words and
adjacent pairs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from .plan import BASIS, Box

# Product shot labels (Bán hàng) and moment labels (Tổng hợp). "unknown" is a valid answer.
PRODUCT_LABELS = ("product_overview", "product_closeup", "product_demonstration", "product_feature", "product_result",
                  "product_packaging")
MOMENT_LABELS = ("action", "reaction", "reveal", "explanation", "establishing", "people", "object", "text_on_screen")
LABELS = PRODUCT_LABELS + MOMENT_LABELS
LABEL_NAMES_VI = {
    "product_overview": "toàn cảnh sản phẩm", "product_closeup": "cận cảnh chi tiết", "product_demonstration": "thao tác sử dụng",
    "product_feature": "trình diễn tính năng", "product_result": "kết quả sử dụng", "product_packaging": "hộp, bao bì",
    "action": "hành động", "reaction": "phản ứng", "reveal": "khoảnh khắc bất ngờ", "explanation": "giải thích",
    "establishing": "toàn cảnh bối cảnh", "people": "con người", "object": "đồ vật", "text_on_screen": "chữ trên hình",
}
# Words a user or a file name uses for a label (folded), so "Cận cảnh lòng nồi" counts as a closeup.
LABEL_CUES = {
    "product_overview": ("toan canh", "tong the", "overview", "full", "goc rong", "mat truoc"),
    "product_closeup": ("can canh", "chi tiet", "closeup", "close up", "macro", "zoom"),
    "product_demonstration": ("thao tac", "su dung", "cach dung", "huong dan", "demo", "lap rap", "bam", "nhan nut"),
    "product_feature": ("tinh nang", "che do", "feature", "cong nghe", "chuc nang"),
    "product_result": ("ket qua", "thanh pham", "result", "sau khi", "thanh qua", "mon an"),
    "product_packaging": ("hop", "bao bi", "unbox", "khui hop", "dong goi", "package"),
}
ORIGINS = ("user", "metadata", "transcript", "vision", "signal")
# How far each origin's own confidence is trusted (milli).
TRUST = {"user": 950, "metadata": 550, "transcript": 800, "vision": 800, "signal": 300}

# Only words that stay function words without their diacritics: "can" (cận/cần/cân), "chi" (chi tiết), "tu" (tủ),
# "da" (da thật), "toi" (tối), "tai" (tai nghe), "ban" (bàn/bán) and "con" carry meaning once folded, so they stay.
_STOP = frozenset("""
va voi cua la khong mot nhung cac nay thi ma se dang duoc bi rat cung
khi neu vi nhu nhat nhieu minh chung no ay kia
nao gi sao thoi nhe nha oi roi luon hay hoac
mp4 mov mkv webm avi m4v jpg jpeg png webp clip video img vid dsc
the an of and to in on with for is are this that it be at by from
""".split())
_WORD = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lower case, without Vietnamese diacritics (đ → d), punctuation as spaces."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text.casefold())
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(_WORD.findall(plain))


def words(text: str) -> list[str]:
    return [w for w in fold(text).split() if w not in _STOP and (len(w) > 1 or w.isdigit())]


_STOP_MARKED = frozenset("""
và với của cho là không một những các này thì mà nên sẽ đang được bị rất cũng lại
khi nếu vì để như hơn nhất mỗi nhiều mình chúng họ nó ấy kia đây
nào gì sao thôi nhé nha ơi rồi luôn hay hoặc cả
mp4 mov mkv webm avi m4v jpg jpeg png webp clip video img vid dsc
the an of and to in on with for is are this that it be at by from
""".split())  # the same function words, written with their marks (so "hồ" is not "họ")
_LETTERS = re.compile(r"[^\W_]+")
LOOSE = "~"  # prefix of a term from text written without diacritics


def _written_with_marks(text: str) -> bool:
    """Whether ``text`` is Vietnamese written with its marks.

    Vietnamese written properly has marks on most words; a few marked words in otherwise plain text (an AI
    answer typed without marks, a file name with one accented word) does not make it marked text.
    """
    found = _LETTERS.findall(text)
    if not found:
        return False
    marked = sum(1 for w in found if any(ord(c) > 127 and c.isalpha() for c in w))
    return marked * 4 >= len(found)  # a quarter of the words or more


def terms(text: str) -> frozenset[str]:
    """Single words and adjacent pairs of ``text``, function words dropped.

    Vietnamese written with its marks keeps them: "cần" and "cận", "đồng" and
    "dòng" are different words. Text written without marks (a file name, an
    unaccented note) gives loose terms (``~`` + the folded words), which match a
    word whatever its marks (see ``match``).
    """
    if _written_with_marks(text):
        found = [w for w in _LETTERS.findall(unicodedata.normalize("NFC", text.casefold()))
                 if w not in _STOP_MARKED and (len(w) > 1 or w.isdigit())]
        prefix = ""
    else:
        found, prefix = words(text), LOOSE
    return frozenset(prefix + w for w in found) | frozenset(f"{prefix}{a} {b}" for a, b in zip(found, found[1:]))


def loose(found: Mapping[str, int]) -> dict[str, int]:
    """Every term of ``found`` as its loose form (best confidence), for matching unaccented text."""
    out: dict[str, int] = {}
    for term, value in found.items():
        key = term if term.startswith(LOOSE) else LOOSE + fold(term)
        out[key] = max(out.get(key, 0), value)
    return out


def match(term: str, found: Mapping[str, int], loose_found: Mapping[str, int]) -> int:
    """The confidence ``found`` gives ``term`` (0 when absent). Marks are ignored only when one side has none."""
    if term in found:
        return found[term]
    if term.startswith(LOOSE):
        return loose_found.get(term, 0)
    return found.get(LOOSE + fold(term), 0)


def file_words(name: str) -> str:
    """A file name as words: separators to spaces, camera counters and hashes dropped."""
    stem = name.rsplit(".", 1)[0]
    stem = re.sub(r"[_\-.+]+", " ", stem)
    return " ".join(w for w in stem.split() if not re.fullmatch(r"[0-9a-f]{8,}|\d{3,}|(img|vid|dsc|mvi)\d*", w.casefold()))


def cue_labels(text: str) -> list[str]:
    """Product labels a user's note or a file name states with its own words."""
    folded = f" {fold(text)} "
    return [label for label, cues in LABEL_CUES.items() if any(f" {cue} " in folded for cue in cues)]


@dataclass(frozen=True)
class Item:
    """One piece of evidence: what (text and/or labels), from where, how sure (milli)."""

    origin: str
    confidence: int
    text: str = ""
    labels: tuple[str, ...] = ()
    detail: str = ""  # e.g. the vision model's name or the transcript line's time

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "confidence": self.confidence, "text": self.text, "labels": list(self.labels),
                "detail": self.detail}


@dataclass
class Evidence:
    """Everything known about one candidate."""

    candidate_id: str
    items: list[Item] = field(default_factory=list)
    product_box: Box | None = None
    hook: int = 0          # 0–1000, from vision when it said so
    product_visible: bool | None = None
    same_product: str | None = None  # vision's group tag for "which product is this"

    def add(self, item: Item) -> None:
        if item.origin not in ORIGINS:
            raise ValueError(f"unknown evidence origin {item.origin!r}")
        if not 0 <= item.confidence <= 1000:
            raise ValueError("confidence must be 0–1000")
        self.items.append(item)

    @property
    def content(self) -> bool:
        """Whether anything says what the candidate shows (not only signals)."""
        return any(item.origin != "signal" and (item.text.strip() or item.labels) for item in self.items)

    def terms(self) -> dict[str, int]:
        """Term → the best trust-weighted confidence any evidence gives it (milli)."""
        out: dict[str, int] = {}
        for item in self.items:
            weight = item.confidence * TRUST[item.origin] // 1000
            # each piece on its own: an AI description typed without marks next to label names with marks
            for text in (item.text, *(LABEL_NAMES_VI.get(label, "") for label in item.labels)):
                for term in terms(text):
                    out[term] = max(out.get(term, 0), weight)
        return out

    def labels(self) -> dict[str, int]:
        """Label → its best trust-weighted confidence (milli)."""
        out: dict[str, int] = {}
        for item in self.items:
            weight = item.confidence * TRUST[item.origin] // 1000
            for label in item.labels:
                if label in LABELS:
                    out[label] = max(out.get(label, 0), weight)
        return out

    def origins(self) -> list[str]:
        return sorted({item.detail or item.origin for item in self.items if item.origin != "signal"})

    def summary(self, limit: int = 160) -> str:
        """The best description for a reason or the desktop: the user's note, else vision's, else anything."""
        for origin in ("user", "vision", "transcript", "metadata"):
            for item in self.items:
                if item.origin == origin and item.text.strip():
                    return " ".join(item.text.split())[:limit]
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "items": [item.to_dict() for item in self.items],
                "product_box": None if self.product_box is None else self.product_box.to_dict(), "hook": self.hook,
                "product_visible": self.product_visible, "same_product": self.same_product}


def transcript_text(cues: Sequence[tuple[int, int, str]], start_ms: int, end_ms: int) -> str:
    """The transcript lines spoken inside ``[start_ms, end_ms)`` (any overlap)."""
    return " ".join(text for a, b, text in cues if a < end_ms and b > start_ms)


_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(text: str) -> list[tuple[int, int, str]]:
    """An SRT file's cues as (start ms, end ms, text); malformed blocks are skipped."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                h1, m1, s1, f1, h2, m2, s2, f2 = (int(g) for g in match.groups())
                start = ((h1 * 60 + m1) * 60 + s1) * 1000 + f1
                end = ((h2 * 60 + m2) * 60 + s2) * 1000 + f2
                body = " ".join(re.sub(r"<[^>]+>|\{[^}]*\}", "", part) for part in lines[index + 1:])
                if end > start and body.strip():
                    cues.append((start, end, " ".join(body.split())))
                break
    return cues


def box_from(value: Any) -> Box | None:
    """A product box from a vision answer ({x, y, w, h} in 0–1000 of the frame), or None when it is not a sane box."""
    if not isinstance(value, Mapping):
        return None
    try:
        x, y, w, h = (int(value[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > 1000 or y + h > 1000 or w * h < 400:  # under 2 % × 2 % is noise
        return None
    return Box(x * BASIS // 1000, y * BASIS // 1000, w * BASIS // 1000, h * BASIS // 1000, "product")


def gather(candidate_ids: Iterable[str]) -> dict[str, Evidence]:
    return {candidate_id: Evidence(candidate_id) for candidate_id in candidate_ids}


__all__ = ["Evidence", "Item", "LABELS", "LABEL_NAMES_VI", "MOMENT_LABELS", "PRODUCT_LABELS", "box_from", "cue_labels",
           "file_words", "fold", "gather", "parse_srt", "terms", "transcript_text", "words"]

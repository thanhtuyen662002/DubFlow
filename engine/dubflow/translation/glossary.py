"""Thuật ngữ và ngữ cảnh (Issue #903): the user's own spellings and background for a video's translation.

Two things the user gives, kept apart:

* **Glossary** (``Term``): a source term and the Vietnamese to use for it, either
  *mandatory* ("bắt buộc": always this spelling) or *preferred* ("ưu tiên": the
  translator's hint). Terms come from the video itself (the *project* glossary)
  and from reusable glossaries the user applies to many videos (*shared*).
* **Context** (``Context``): what the video is about, for whom, its tone, the
  people in it and how they address each other, and free notes.

Priority, highest first (``PRIORITY``)::

    the user's direct edit of a line (translation_overrides.json, applied after this)
    > mandatory project term > mandatory shared term
    > preferred project term > preferred shared term
    > the user's context > the automatic brief (context_ai) > the default prompt

Where it is used: only the translation step. The offline translator's lines are
checked term by term (``enforce``): a mandatory or preferred term whose source
is in the line but whose Vietnamese is not gets the offline translator's own
rendering of that term replaced, when the line holds it; otherwise the line is
counted as unconfirmed and left for review (never guessed). The optional
in-context AI pass (the user's own key) gets the terms and the context in its
brief and prompt (``brief_lines``, ``merge_brief_terms``). Nothing here calls a
service, and nothing here touches voice, mixing or rendering.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any

MAX_TERMS = 500
MAX_TERM_CHARS = 80
MAX_NOTE_CHARS = 200
MAX_SHARED = 10
MAX_CONTEXT_CHARS = {"topic": 300, "audience": 120, "tone": 120, "people": 800, "notes": 800}

PRIORITY = (
    "user_edit",
    "mandatory_project",
    "mandatory_shared",
    "preferred_project",
    "preferred_shared",
    "user_context",
    "auto_brief",
    "default_prompt",
)


class GlossaryError(ValueError):
    pass


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(ch for ch in _nfc(value) if ch == "\n" or not unicodedata.category(ch).startswith("C"))
    return " ".join(text.split())[:limit]


def _key(text: str) -> str:
    """How two sources are compared: NFC, case-folded, spaces collapsed."""
    return " ".join(_nfc(text).casefold().split())


@dataclass(frozen=True)
class Term:
    source: str
    target: str
    mandatory: bool = False
    note: str = ""
    # "project" or "shared:<glossary name>": where it came from, for conflicts and the report.
    origin: str = "project"

    @property
    def level(self) -> str:
        return ("mandatory_" if self.mandatory else "preferred_") + ("project" if self.origin == "project" else "shared")


@dataclass(frozen=True)
class Context:
    topic: str = ""
    audience: str = ""
    tone: str = ""
    people: str = ""
    notes: str = ""

    def empty(self) -> bool:
        return not any((self.topic, self.audience, self.tone, self.people, self.notes))

    def lines(self) -> list[str]:
        labels = (("topic", "Chủ đề"), ("audience", "Người xem"), ("tone", "Giọng điệu"), ("people", "Nhân vật và cách xưng hô"), ("notes", "Ghi chú"))
        return [f"{label}: {getattr(self, name)}" for name, label in labels if getattr(self, name)]


@dataclass(frozen=True)
class Conflict:
    source: str
    kept: Term
    dropped: Term


@dataclass(frozen=True)
class Guide:
    """The resolved glossary (one term per source, highest priority first) and the context."""
    terms: tuple[Term, ...] = ()
    context: Context = field(default_factory=Context)
    conflicts: tuple[Conflict, ...] = ()
    duplicates: int = 0

    def empty(self) -> bool:
        return not self.terms and self.context.empty()

    def identity(self) -> str:
        """Changes when anything the translation reads changes (not notes or origins)."""
        value = {"terms": [[term.source, term.target, term.mandatory] for term in self.terms],
                 "context": [self.context.topic, self.context.audience, self.context.tone, self.context.people, self.context.notes]}
        return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _terms(rows: Any, origin: str) -> list[Term]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise GlossaryError("terms must be a list")
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise GlossaryError("each term must be an object")
        source, target = _clean(row.get("source"), MAX_TERM_CHARS), _clean(row.get("target"), MAX_TERM_CHARS)
        if not source or not target:
            continue  # a half-filled row in the editor is not a term yet
        result.append(Term(source, target, row.get("mandatory") is True, _clean(row.get("note"), MAX_NOTE_CHARS), origin))
    return result


def _context(value: Any) -> Context:
    if value is None:
        return Context()
    if not isinstance(value, Mapping):
        raise GlossaryError("context must be an object")
    return Context(**{name: _clean(value.get(name), limit) for name, limit in MAX_CONTEXT_CHARS.items()})


def resolve(document: Any) -> Guide:
    """``{"project": [terms], "shared": [{"name", "terms"}], "context": {...}}`` as one ``Guide``.

    One term per source (compared case- and space-insensitively): the highest
    level wins; within a level, the first listed. Two different spellings for one
    source are a conflict, kept for the report; the same spelling twice is a duplicate.
    """
    if document is None:
        return Guide()
    if not isinstance(document, Mapping):
        raise GlossaryError("glossary must be an object")
    candidates = _terms(document.get("project"), "project")
    shared = document.get("shared") or []
    if not isinstance(shared, list):
        raise GlossaryError("shared must be a list")
    for book in shared[:MAX_SHARED]:
        if not isinstance(book, Mapping):
            raise GlossaryError("each shared glossary must be an object")
        name = _clean(book.get("name"), 60) or "Bộ thuật ngữ"
        candidates.extend(_terms(book.get("terms"), f"shared:{name}"))
    rank = {level: index for index, level in enumerate(PRIORITY)}
    ordered = sorted(enumerate(candidates), key=lambda pair: (rank[pair[1].level], pair[0]))
    chosen: dict[str, Term] = {}
    conflicts: list[Conflict] = []
    duplicates = 0
    for _, term in ordered:
        key = _key(term.source)
        kept = chosen.get(key)
        if kept is None:
            chosen[key] = term
        elif _key(kept.target) == _key(term.target):
            duplicates += 1
        else:
            conflicts.append(Conflict(term.source, kept, term))
    terms = tuple(chosen.values())[:MAX_TERMS]
    return Guide(terms, _context(document.get("context")), tuple(conflicts), duplicates)


# ---------- matching ----------

_WORD = re.compile(r"\w", re.UNICODE)


def _is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯"


# Letters and digits that make a Latin word longer (Vietnamese included); a CJK character beside a Latin
# term ("这块GPU很快") is not part of it.
_LATIN = r"0-9A-Za-z_\u00C0-\u024F\u1E00-\u1EFF"


def _pattern(term: str) -> re.Pattern[str]:
    """A whole-word, case-insensitive match; CJK text has no spaces, so it matches anywhere."""
    text = re.escape(_nfc(term).strip()).replace(r"\ ", r"\s+")
    head = "" if _is_cjk(term.strip()[0]) else rf"(?<![{_LATIN}])"
    tail = "" if _is_cjk(term.strip()[-1]) else rf"(?![{_LATIN}])"
    return re.compile(head + text + tail, re.IGNORECASE | re.UNICODE)


def contains(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(_pattern(term).search(_nfc(text)))


def _keep_case(found: str, replacement: str) -> str:
    """"Anh" for "anh" at the start of a sentence; a target with its own capitals is kept as written."""
    if replacement != replacement.lower():
        return replacement
    if found[:1].isupper() and replacement:
        return replacement[0].upper() + replacement[1:]
    return replacement


@dataclass(frozen=True)
class Enforced:
    cues: tuple[Any, ...]
    replaced: int = 0
    # (cue id, term source): a term whose source is in the line but whose Vietnamese could not be confirmed there.
    unconfirmed: tuple[tuple[str, str], ...] = ()


def enforce(cues: Sequence[Any], guide: Guide, render: Callable[[str], str] | None = None) -> Enforced:
    """Every line whose source holds a term uses the term's Vietnamese.

    ``render`` is the offline translator for one term (cached by the caller); the
    line's copy of that rendering becomes the term's Vietnamese. A line that
    already has the Vietnamese is left alone; one where it cannot be placed is
    reported in ``unconfirmed`` (mandatory terms only: a preferred term is a hint).
    """
    rendered: dict[str, str] = {}
    result = []
    replaced = 0
    unconfirmed: list[tuple[str, str]] = []
    for cue in cues:
        text = cue.translated_text or ""
        for term in guide.terms:
            if not contains(cue.source_text, term.source) or contains(text, term.target):
                continue
            if render is not None and term.source not in rendered:
                try:
                    rendered[term.source] = _clean(render(term.source), MAX_TERM_CHARS)
                except Exception:  # noqa: BLE001 - one odd term must not stop the others
                    rendered[term.source] = ""
            offline = rendered.get(term.source, "")
            # The term itself left untranslated (a name kept in Latin letters, say) is the other usual miss.
            for wrong in filter(None, (offline, term.source)):
                if _key(wrong) == _key(term.target) or not contains(text, wrong):
                    continue
                text = _pattern(wrong).sub(lambda match: _keep_case(match.group(0), term.target), text)
                replaced += 1
                break
            else:
                if term.mandatory:
                    unconfirmed.append((cue.cue_id, term.source))
        result.append(replace(cue, translated_text=text) if text != (cue.translated_text or "") else cue)
    return Enforced(tuple(result), replaced, tuple(unconfirmed))


# ---------- the AI pass ----------

def merge_brief_terms(brief: Any, guide: Guide, sources: Iterable[str] | None = None) -> Any:
    """The automatic brief with the user's terms (those ``sources`` say) in place of its own for the same source."""
    text = "\n".join(sources) if sources is not None else None
    used = [term for term in guide.terms if text is None or contains(text, term.source)]
    if not used:
        return brief
    value = dict(brief) if isinstance(brief, Mapping) else {}
    mine = {_key(term.source) for term in used}
    automatic = [item for item in (value.get("thuat_ngu") or []) if isinstance(item, Mapping) and _key(str(item.get("goc", ""))) not in mine]
    value["thuat_ngu"] = [{"goc": term.source, "viet": term.target} for term in used] + automatic
    return value


def brief_lines(guide: Guide, sources: Iterable[str] | None = None) -> str:
    """What the AI is told before the automatic brief: the user's context, then the terms that occur in ``sources``."""
    text = "\n".join(sources) if sources is not None else None
    used = [term for term in guide.terms if text is None or contains(text, term.source)]
    lines = []
    if not guide.context.empty():
        lines.append("Bối cảnh do người dùng ghi (đúng hơn phần tóm tắt tự động nếu khác nhau):")
        lines.extend("- " + line for line in guide.context.lines())
    must = [f"{term.source} → {term.target}" for term in used if term.mandatory]
    prefer = [f"{term.source} → {term.target}" for term in used if not term.mandatory]
    if must:
        lines.append("Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: " + "; ".join(must[:120]))
    if prefer:
        lines.append("Thuật ngữ nên dùng (trừ khi sai ngữ cảnh): " + "; ".join(prefer[:120]))
    return "\n".join(lines)[:6000]

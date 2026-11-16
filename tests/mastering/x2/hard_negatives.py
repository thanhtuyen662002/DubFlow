"""Why hard negatives get footage, and an offline contrast check (Workstream C, V3 §VI). Research only.

ENGINEERING_FIXTURE_ONLY = TRUE for the fixtures; the d-s1 probes are agent-written and stay on the test machine.
Nothing here is wired into the planner, verification or Final QC, and it is not #844.

Four things are kept apart for each hard negative (a sentence whose footage does not exist, by construction):
1. *offered*: some shot reaches the planner's minimum relevance (it is a candidate);
2. *assigned*: the planner says it found matching footage (``coverage.matched_footage``);
3. *reviewed*: the sentence is NEEDS_REVIEW (or MATCH_NOT_FOUND), never READY;
4. *blocked*: Final QC on the whole part does not pass while the sentence is unreviewed.

``contrast`` is a prototype of a check that could explain an assignment to a reviewer: it compares the content words
of the sentence with those of the cue that matched it.
- ``number_conflict``: both name a number (a year) and they differ;
- ``substitution``: each side has a content word the other lacks while they share some (another entity or another
  action in the same frame: "the helicopter climbs the mountain" over "the locomotive climbs the mountain");
- ``consistent``: neither. A cue is long and says more than a sentence, so the cue side alone is never a signal.
It can only add a reason to look; it must never remove one or raise a confidence.
"""
from __future__ import annotations

import json, re, sys, unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.dubflow.mastering import evidence as ev  # noqa: E402

ENGINEERING_FIXTURE_ONLY = True
_NUMBER = re.compile(r"^\d+$")
# words of a narrator's frame, not of what is shown (fixture sentences start "We watch ..."; d-s1 "Next, ...")
_FRAME = frozenset({"we", "watch", "see", "look", "next", "then", "finally", "now", "here", "again", "also"})


def _stem(word: str) -> str:
    if word.endswith("ss"):  # cross, glass: no plural "s" to take off
        return word
    if word.endswith(("sses", "shes", "ches", "xes")):  # crosses, flashes, reaches, boxes
        return word[:-2]
    for suffix in ("ing", "ed", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def content(text: str) -> tuple[set[str], set[str]]:
    """(content word stems, bare numbers) of ``text``. A word holding a digit is left to :func:`quantities`."""
    words = [w for w in ev.words(text) if w not in _FRAME]
    return {_stem(w) for w in words if not any(ch.isdigit() for ch in w)}, {w for w in words if _NUMBER.match(w)}


# --- numbers as written in specs (#873) ---------------------------------------------------------------------------
# unit (lower case) -> (dimension, factor to the dimension's base unit)
UNITS: dict[str, tuple[str, float]] = {
    "mg": ("mass", 0.001), "g": ("mass", 1), "gr": ("mass", 1), "gram": ("mass", 1), "kg": ("mass", 1000),
    "mm": ("length", 0.001), "cm": ("length", 0.01), "m": ("length", 1), "km": ("length", 1000),
    "in": ("inch", 1), "inch": ("inch", 1), "inches": ("inch", 1),
    "w": ("power", 1), "kw": ("power", 1000), "mah": ("charge", 1), "wh": ("energy", 1),
    "x": ("zoom", 1), "k": ("video_k", 1), "hz": ("frequency", 1), "ghz": ("frequency", 1e9),
    "gb": ("bytes", 1), "tb": ("bytes", 1000), "mp": ("megapixel", 1), "chấm": ("megapixel", 1),
    "nits": ("nits", 1), "nit": ("nits", 1), "%": ("percent", 1), "°": ("degree", 1), "độ": ("degree", 1),
    "tháng": ("month", 1), "month": ("month", 1), "months": ("month", 1), "năm": ("year", 1), "year": ("year", 1),
    "years": ("year", 1), "ngày": ("day", 1), "day": ("day", 1), "days": ("day", 1), "màu": ("colour", 1),
    "lần": ("times", 1), "times": ("times", 1),
}
# Vietnamese number words, read only right before a unit ("một tháng", "hai màu"); "năm" is also "year"
NUMBER_WORDS = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10}
_NUM = re.compile(r"(?<![\w.,])([A-Za-z]*)(\d+(?:[.,]\d+)*)([A-Za-z%°]*)(?![\w])")
_WORD = re.compile(r"[^\W\d_]+|[%°]", re.UNICODE)


def _vietnamese(text: str) -> bool:
    return any(ch.isalpha() and ord(ch) > 127 for ch in text)


def _plain(word: str) -> str:
    """``word`` lower-cased without diacritics (``chẫm`` -> ``cham``, ``đồng`` -> ``dong``)."""
    word = word.lower().replace("đ", "d")
    return "".join(ch for ch in unicodedata.normalize("NFD", word) if not unicodedata.combining(ch))


# units of three letters or more, keyed without diacritics: a word that only matches here was misheard or mistyped
_UNIT_LOOKALIKES = {_plain(u) for u in UNITS if len(_plain(u)) >= 3}


def _garbled_unit(word: str) -> bool:
    """``word`` is not a unit but looks like one once diacritics go, or after the common ASR swap tr -> ch."""
    w = word.lower()
    if w in UNITS:
        return False
    plain = _plain(w)
    return plain in _UNIT_LOOKALIKES or (plain.startswith("tr") and "ch" + plain[2:] in _UNIT_LOOKALIKES)


def parse_number(raw: str, vietnamese: bool) -> float | None:
    """``raw`` digits with ``.``/``,`` -> value. Exactly three digits after one separator is a thousands group by the
    text's convention (vi: ``1.800`` = 1800, ``0,198`` = 0.198; en: ``631,000`` = 631000, ``1.800`` = 1.8)."""
    parts = re.split(r"([.,])", raw)
    seps = parts[1::2]
    if not seps:
        return float(raw)
    thousands, decimal = (".", ",") if vietnamese else (",", ".")
    if len(set(seps)) == 1 and len(seps) > 1:  # 1.800.000
        return float("".join(parts[0::2])) if seps[0] == thousands else None
    if len(seps) == 1:
        head, sep, tail = parts
        if len(tail) == 3 and sep == thousands and len(head) <= 3:
            return float(head + tail)
        return float(head + "." + tail)
    if all(x == thousands for x in seps[:-1]) and seps[-1] == decimal:  # 1.234,5 (vi)
        return float("".join(parts[0:-2:2]) + "." + parts[-1])
    return None


def quantities(text: str, lang: str | None = None) -> dict[str, list]:
    """What ``text`` says in numbers: ``measures`` [(dimension, base value, value as written)], ``bare`` [value] (no known unit),
    ``models`` [(letter prefix, id)] (``X9``, ``IP68``), ``unknown`` [raw] (a number this parser cannot read).
    ``lang`` (``"vi"``/``"en"``) is the text's source language; without it, diacritics decide."""
    vi = lang == "vi" if lang else _vietnamese(text)
    out: dict[str, list] = {"measures": [], "bare": [], "models": [], "unknown": []}
    for m in _NUM.finditer(text):
        prefix, raw, suffix = m.group(1), m.group(2), m.group(3)
        if prefix:
            out["models"].append((prefix.upper(), (prefix + raw + suffix).upper()))
            continue
        value = parse_number(raw, vi)
        if value is None:
            out["unknown"].append(raw)
            continue
        unit = suffix.lower()
        if not unit:
            nxt = _WORD.match(text, len(text) - len(text[m.end():].lstrip()))
            word = nxt.group(0).lower() if nxt else ""
            if _garbled_unit(word):  # "64 chẫm": the unit is there but unreadable
                out["unknown"].append(f"{raw} {word}")
                continue
            unit = word if word in UNITS else ""
        if unit in UNITS:
            dim, factor = UNITS[unit]
            out["measures"].append((dim, value * factor, value))
        elif unit:
            out["unknown"].append(raw + suffix)
        else:
            out["bare"].append(value)
    # number words right before a unit; "sáu phẩy năm in" is 6.5 inch, "sáu phẩy in" (no decimal part) is unknown
    tokens = [t.group(0).lower() for t in _WORD.finditer(text)]
    for i, w in enumerate(tokens):
        if w not in NUMBER_WORDS or (i and tokens[i - 1] == "phẩy"):
            continue
        after = tokens[i + 1:i + 4]
        if after[:1] == ["phẩy"]:
            if len(after) == 3 and after[1] in NUMBER_WORDS and after[2] in UNITS:
                value = float(f"{NUMBER_WORDS[w]}.{NUMBER_WORDS[after[1]]}")
                dim, factor = UNITS[after[2]]
                out["measures"].append((dim, value * factor, value))
            else:
                out["unknown"].append(" ".join([w] + after[:2]))
        elif after and after[0] in UNITS and after[0] not in NUMBER_WORDS:
            dim, factor = UNITS[after[0]]
            out["measures"].append((dim, NUMBER_WORDS[w] * factor, NUMBER_WORDS[w]))
    return out


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def compare_numbers(sentence: str, cue: str, langs: tuple[str | None, str | None] | None = None) -> dict[str, Any]:
    """``conflict`` when the sentence states a value the cue states otherwise (same dimension, same bare-number frame,
    same model letters); ``unknown`` when a sentence number has nothing in the cue to be checked against, or cannot be
    read; ``agree`` when every sentence number is found; ``none`` when the sentence has no number."""
    s_lang, c_lang = langs or (None, None)
    # a side without a language whose numbers read differently in vi and en ("5.000"): try both readings
    # (when both sides are undecided they are read in the same language: a pair of texts is one transcript or one script)
    s_read, c_read = _readings(sentence, s_lang), _readings(cue, c_lang)
    both = len(s_read) > 1 and len(c_read) > 1
    readings = {(a, a) for a in s_read} if both else {(a, b) for a in s_read for b in c_read}
    if len(readings) > 1:
        outcomes = {k: _compare(sentence, cue, k) for k in sorted(readings, key=str)}
        states = {o["state"] for o in outcomes.values()}
        if len(states) > 1:
            first = outcomes[min(outcomes, key=str)]
            raws = [r for t, lang in ((sentence, s_lang), (cue, c_lang)) for r in _ambiguous(t, lang)]
            return {**first, "state": "unknown", "conflicts": [], "unknown": first["unknown"] + [f"lang?:{r}" for r in raws]}
    return _compare(sentence, cue, (s_lang, c_lang))


def _ambiguous(text: str, lang: str | None) -> list[str]:
    """Numbers in ``text`` that read differently in vi and en, when no language is given and diacritics cannot tell."""
    if lang or _vietnamese(text):
        return []
    return [m.group(2) for m in _NUM.finditer(text) if not m.group(1) and parse_number(m.group(2), True) != parse_number(m.group(2), False)]


def _readings(text: str, lang: str | None) -> list[str | None]:
    return ["vi", "en"] if _ambiguous(text, lang) else [lang]


def _compare(sentence: str, cue: str, langs: tuple[str | None, str | None]) -> dict[str, Any]:
    s_lang, c_lang = langs
    s, c = quantities(sentence, s_lang), quantities(cue, c_lang)
    conflicts, unknown = [], list(s["unknown"])
    cue_bare = c["bare"]
    for dim, value, written in s["measures"]:
        same_dim = [v for d, v, _ in c["measures"] if d == dim]
        # a cue number without a unit is checked against the number as the sentence writes it ("8.4 mm" / "8,4")
        if any(_same(value, v) for v in same_dim) or (not same_dim and any(_same(written, v) for v in cue_bare)):
            continue
        (conflicts if same_dim else unknown).append(f"{dim}:{written:g}")
    for value in s["bare"]:
        if any(_same(value, v) for v in cue_bare + [w for _, _, w in c["measures"]]):
            continue
        (conflicts if cue_bare else unknown).append(f"{value:g}")
    cue_ids = {i for _, i in c["models"]}
    for prefix, ident in s["models"]:
        if ident in cue_ids:
            continue
        (conflicts if any(p == prefix for p, _ in c["models"]) else unknown).append(ident)
    said = s["measures"] or s["bare"] or s["models"] or s["unknown"]
    state = "conflict" if conflicts else ("unknown" if unknown else ("agree" if said else "none"))
    return {"state": state, "conflicts": conflicts, "unknown": unknown, "sentence": s, "cue": c}


def contrast(sentence: str, cue: str, langs: tuple[str | None, str | None] | None = None) -> dict[str, Any]:
    """``verdict``: ``number_conflict`` > ``number_unknown`` > ``substitution`` > ``consistent``. ``number`` and
    ``words`` keep the two checks apart (a number verdict hides a word substitution in ``verdict`` only).
    ``langs`` = (sentence language, cue language) when the caller knows them; otherwise each text's diacritics decide."""
    s_words, _ = content(sentence)
    c_words, _ = content(cue)
    shared = s_words & c_words
    only_s, only_c = s_words - c_words, c_words - s_words
    words = "substitution" if shared and only_s and only_c else "consistent"
    number = compare_numbers(sentence, cue, langs)
    verdict = {"conflict": "number_conflict", "unknown": "number_unknown"}.get(number["state"], words)
    return {"verdict": verdict, "number": number["state"], "words": words, "shared": sorted(shared),
            "sentence_only": sorted(only_s), "cue_only": sorted(only_c),
            "numbers": {"conflicts": number["conflicts"], "unknown": number["unknown"]}}


def best_cue(sentence: str, cues: Sequence[tuple[int, int, str]], start_ms: int, end_ms: int) -> str | None:
    """The cue inside ``[start_ms, end_ms)`` sharing the most words with the sentence."""
    wanted = ev.terms(sentence)
    inside = [(len(ev.terms(t) & wanted), -a, t) for a, b, t in cues if a < end_ms and b > start_ms]
    best = max(inside, default=None)
    return best[2] if best and best[0] else None


def stages(rows: Sequence[Mapping[str, Any]], *, final_qc_blocks: bool, min_relevance: int = 60) -> dict[str, Any]:
    """The four stages for the hard negatives among ``rows`` (solo_bench unit rows)."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        if "false_assignment" not in row:
            continue
        kind = out.setdefault(row["kind"], {"units": 0, "offered": 0, "assigned": 0, "reviewed": 0, "ready": 0})
        kind["units"] += 1
        kind["offered"] += (row.get("best_relevance") or 0) >= min_relevance
        kind["assigned"] += bool(row["matched_footage"])
        kind["reviewed"] += row["state"] in ("NEEDS_REVIEW", "MATCH_NOT_FOUND", "SOURCE_INSUFFICIENT")
        kind["ready"] += row["state"] == "READY"
    return {"by_kind": out, "final_qc_blocks_part": final_qc_blocks}


def solo(work: Path) -> dict[str, Any]:
    """solo_bench's hard negatives through the four stages, and ``contrast`` on every assigned unit (match or not)."""
    from tests.mastering.x2 import solo_bench as sb
    result = sb.run(work)
    said = sb.cues()
    checks = []
    for row in result["units"]:
        if not row["matched_footage"] or not row["played"]:
            continue
        _, start, end = row["played"][0]
        shot = start // sb.SHOT_MS
        cue = best_cue(next(u.text for u in sb.units() if u.uid == row["unit"]), said, shot * sb.SHOT_MS, (shot + 1) * sb.SHOT_MS)
        if cue:
            checks.append({"unit": row["unit"], "kind": row["kind"],
                           **contrast(next(u.text for u in sb.units() if u.uid == row["unit"]), cue)})
    return {"ENGINEERING_FIXTURE_ONLY": True,
            "stages": stages(result["units"], final_qc_blocks=not result["summary"]["final_qc"]["decisions"]),
            "contrast": checks, "summary": score(checks)}


def score(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    negatives = [c for c in checks if c["kind"] not in ("match", "montage", "pos")]
    positives = [c for c in checks if c["kind"] in ("match", "montage", "pos")]
    return {"negatives_assigned": len(negatives),
            "negatives_flagged": sum(c["verdict"] != "consistent" for c in negatives),
            "positives_assigned": len(positives),
            "positives_flagged": sum(c["verdict"] != "consistent" for c in positives)}


if __name__ == "__main__":
    out = solo(Path(sys.argv[1] if len(sys.argv) > 1 else "solo-bench"))
    print(json.dumps(out, indent=1, ensure_ascii=False))

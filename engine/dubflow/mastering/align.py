"""Script units and the footage that illustrates each one (ADR-0054 §4–6).

A narration is spoken sentence by sentence; each sentence is a *unit* on the
output timeline, from the middle of the pause before it to the middle of the
pause after it (the first from 0, the last to the end), so pictures change
between sentences, not inside a word. Each unit has a *function* (hook,
benefit, demonstration, feature, result, call to action, or plain narration)
from its position and its own words.

For a unit, every candidate gets a score in milli:

* relevance (45 %): the unit's terms found in the candidate's evidence, each
  weighted by how rare it is among the candidates (a product name on every clip
  says little) and by the evidence's trust;
* role fit (25 %): how well the candidate's labels suit the unit's function;
* quality (15 %), length fit (5 %);
* motion only as a small extra for hooks and quick narration (never alone);
* minus reuse, duplicate and same-source-as-before penalties.

Units are filled in order. A workflow without ``reorder`` keeps each source
playing forward. A user's edits pin candidates to a unit or exclude candidates
or sources before scoring. When no candidate has any content evidence, the
result is *signals only*: chosen by quality and diversity, every piece capped at
confidence 450 and marked for review, and the decision log says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from . import claims as said
from . import entities as names
from . import evidence as ev
from .candidates import Candidate
from .frames import parent
from .policy import Pacing

FUNCTIONS = ("hook", "benefit", "demonstration", "feature", "result", "cta", "narration", "intro", "outro")
SIGNALS_ONLY_CAP = 450
REVIEW_BELOW = 600
REUSE_PENALTY = 350
STRANDED_PENALTY = 300  # under a full content match (450): a clear match still wins, a weak one yields
DUPLICATE_PENALTY = 250
SAME_SOURCE_PENALTY = 100
CLAIM_PENALTY = 250  # a shot a later sentence matches far better is left for that sentence
STRONG_SHARE = 850  # a shot within this share (‰) of the best match counts as the match, short or not
CLAIM_SHARE = 500  # a later sentence claims the shots it matches at least this share (‰) of its best match
SHORT_FLOOR = 500  # ms: the shortest piece of a strong match worth showing (anything less is a flash)

# Folded cue words for a sentence's function (Vietnamese sales language).
CUES = {
    "cta": ("mua ngay", "dat hang", "dat mua", "chot don", "inbox", "lien he", "link", "gio hang", "nhanh tay", "uu dai",
            "giam gia", "khuyen mai", "qua tang", "chi con", "so luong co han", "freeship", "mien phi van chuyen", "hom nay"),
    "demonstration": ("chi can", "cho vao", "bam", "nhan", "xoay", "lap", "thao", "cach dung", "su dung", "dat vao", "do vao",
                      "cam dien", "bat", "mo nap", "dong nap", "keo", "van"),
    "feature": ("dung tich", "cong suat", "chat lieu", "chong dinh", "tinh nang", "che do", "cong nghe", "thiet ke", "kich thuoc",
                "trong luong", "pin", "inox", "lit", "watt", "w ", "cam ung", "man hinh", "bao hanh"),
    "result": ("ket qua", "gion", "thom", "sach", "chin deu", "vang deu", "mem", "min", "thanh pham", "sau khi", "chi sau",
               "trong vong", "tiet kiem", "nhanh chong"),
}
FIT: Mapping[str, Mapping[str, int]] = {
    "hook": {"product_result": 1000, "product_demonstration": 850, "product_feature": 800, "product_closeup": 700,
             "product_overview": 550, "reveal": 1000, "action": 900, "reaction": 800},
    "benefit": {"product_result": 900, "product_demonstration": 800, "product_overview": 650, "product_closeup": 550,
                "product_feature": 600},
    "demonstration": {"product_demonstration": 1000, "product_feature": 700, "product_closeup": 450, "action": 700},
    "feature": {"product_feature": 1000, "product_closeup": 900, "product_demonstration": 700},
    "result": {"product_result": 1000, "product_demonstration": 500, "product_closeup": 400},
    "cta": {"product_overview": 1000, "product_packaging": 900, "product_closeup": 600, "product_result": 600},
    "intro": {"establishing": 1000, "people": 600, "action": 600, "object": 500},
    "narration": {"action": 800, "reveal": 800, "explanation": 750, "reaction": 600, "establishing": 500, "people": 500,
                  "object": 500, "text_on_screen": 400},
    "outro": {"reaction": 900, "establishing": 800, "people": 600, "action": 500},
}


@dataclass(frozen=True)
class Unit:
    unit_id: str
    text: str
    start_ms: int
    end_ms: int
    function: str

    @property
    def length_ms(self) -> int:
        return self.end_ms - self.start_ms


def product_function(index: int, count: int, text: str) -> str:
    """A sales sentence's function: the first is the hook; then by its cue words; otherwise a benefit."""
    if index == 0:
        return "hook"
    folded = f" {ev.fold(text)} "
    if any(f" {cue} " in folded for cue in CUES["cta"]) and index >= count // 2:
        return "cta"
    for function in ("demonstration", "result", "feature"):
        if any(f" {cue.strip()} " in folded for cue in CUES[function]):
            return function
    return "benefit"


def words_digest(unit_id: str, text: str) -> str:
    """The short digest of a sentence (its id and words) an approval is given on (P2-N5)."""
    return hashlib.sha256(json.dumps([unit_id, text], ensure_ascii=False).encode("utf-8")).hexdigest()[:8]


def units(spoken: Sequence[tuple[str, int]], total_ms: int, *, lead_ms: int, gap_ms: int,
          function: Any = None, prefix: str = "u") -> list[Unit]:
    """The sentences as contiguous units covering ``[0, total_ms)``; cuts sit in the middle of each pause."""
    if not spoken:
        return []
    starts, cursor = [], lead_ms
    for _, duration in spoken:
        starts.append(cursor)
        cursor += duration + gap_ms
    bounds = [0] + [start - gap_ms // 2 for start in starts[1:]] + [total_ms]
    classify = function or (lambda index, count, text: "narration")
    made = []
    for index, (text, _) in enumerate(spoken):
        if bounds[index + 1] > bounds[index]:
            made.append(Unit(f"{prefix}{index + 1}", text, bounds[index], bounds[index + 1], classify(index, len(spoken), text)))
    return made


@dataclass
class Edits:
    """A user's changes to the choice of footage (``editorial.edits``)."""

    pin: dict[str, list[str]] = field(default_factory=dict)   # unit id → candidate ids, in order
    exclude: set[str] = field(default_factory=set)             # candidate ids
    exclude_sources: set[str] = field(default_factory=set)     # source ids
    # approvals (P2-N5): unit id → the candidate ids the user approved, kept as constraints of a new plan. Never
    # part of ``to_dict`` (the plan's identity): an approval is of the footage the plan already shows, so it does
    # not make the plan another one, and approving alone never plans or renders again.
    # Each kept piece is (candidate id, source offset ms, length ms, shown again); an offset of -1 (an approval saved before the
    # pieces were logged) keeps the shot wherever it can play.
    keep: dict[str, list[tuple[str, int, int, bool]]] = field(default_factory=dict)
    # the sentence each approval was given on (:func:`words_digest`): an approval of a sentence whose words changed
    # (a line added above shifts every unit id) is no constraint, it was given to other words. Not in ``to_dict``.
    keep_words: dict[str, str] = field(default_factory=dict)
    # the footage each exclusion was made on (#805): excluded id → the spans (source id, start ms, end ms) the id named
    # when the user left it out (an id excluded again after a renumbering names more footage). A new split numbers its
    # pieces again and a new trim numbers its shots again; the footage, not the id, is what was left out. Not in
    # ``to_dict`` (the ids are): a plan's identity adds them (:func:`ranges_digest_input`).
    exclude_ranges: dict[str, tuple[tuple[str, int, int], ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Any) -> "Edits":
        if value is None:
            return cls()
        if not isinstance(value, Mapping) or set(value) - {"pin", "exclude", "exclude_sources"}:
            raise ValueError("editorial.edits must have only pin, exclude, exclude_sources")
        pin = value.get("pin", {})
        if not isinstance(pin, Mapping) or len(pin) > 500:
            raise ValueError("editorial.edits.pin must map unit ids to candidate ids")
        out = cls()
        for unit_id, ids in pin.items():
            if type(unit_id) is not str or not isinstance(ids, list) or len(ids) > 20 or not all(type(i) is str and i for i in ids):
                raise ValueError("editorial.edits.pin must map unit ids to candidate ids")
            out.pin[unit_id] = list(ids)
        for name in ("exclude", "exclude_sources"):
            ids = value.get(name, [])
            if not isinstance(ids, list) or len(ids) > 2000 or not all(type(i) is str and i for i in ids):
                raise ValueError(f"editorial.edits.{name} must be a list of ids")
            setattr(out, name, set(ids))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"pin": self.pin, "exclude": sorted(self.exclude), "exclude_sources": sorted(self.exclude_sources)}


@dataclass(frozen=True)
class Choice:
    unit_id: str
    candidate: Candidate
    offset_ms: int
    length_ms: int
    score: int
    confidence: int
    needs_review: bool
    reasons: tuple[str, ...]


@dataclass
class Decision:
    """The log of one unit: what was chosen, the next best, and why."""

    unit: Unit
    chosen: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    pieces: list[dict[str, Any]] = field(default_factory=list)  # what plays: shot, source offset, length (P2-N5)

    def to_dict(self) -> dict[str, Any]:
        out = {"unit_id": self.unit.unit_id, "text": self.unit.text, "function": self.unit.function,
               "start_ms": self.unit.start_ms, "end_ms": self.unit.end_ms, "chosen": self.chosen,
               "alternatives": self.alternatives, "notes": self.notes, "coverage": self.coverage}
        if self.signals:
            out["signals"] = self.signals
        if self.pieces:
            out["pieces"] = self.pieces
        return out


MIN_RELEVANCE = 60  # below this the best match is noise (one common word): no rescaling
OPENING_CLOSING = ("hook", "intro", "cta", "outro")
CARD_PENALTY = 300  # a title or end screen for a sentence that does not speak of what is written
COMMON_SHARE = 500  # ‰ of the described shots a word is in before it tells no shot from another
COMMON_MIN_SHOTS = 4  # with fewer described shots every word is "common": the rule needs a few


def _texts(items: Iterable[ev.Item]) -> dict[str, int]:
    """Text → its best trust-weighted confidence (milli), for the names it holds."""
    texts: dict[str, int] = {}
    for item in items:
        weight = item.confidence * ev.TRUST[item.origin] // 1000
        texts[item.text] = max(texts.get(item.text, 0), weight)
    return texts


def _terms_without_speech(found: ev.Evidence) -> dict[str, int]:
    """``found.terms()`` without the speech over the shot (#836)."""
    return ev.Evidence(found.candidate_id, [i for i in found.items if ev.verifies(i) or i.origin != "transcript"]).terms()


class Scorer:
    """Scores candidates for units from their evidence (idf over the candidates of this job)."""

    def __init__(self, candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence]) -> None:
        self.candidates = list(candidates)
        self.evidence = evidence
        self.terms = {c.candidate_id: evidence[c.candidate_id].terms() if c.candidate_id in evidence else {} for c in self.candidates}
        self.loose = {key: ev.loose(found) for key, found in self.terms.items()}
        # what the evidence that can verify the picture alone says (P2-14, #836): a shot matched only through its file
        # name or the speech over it is ranked by them, never trusted for them; only candidates that carry such
        # evidence need the second set (``verified``), and only those with speech the third (``unspoken``: what is
        # known of the shot without the speech, its file name kept, for the coverage)
        self.named = {c.candidate_id for c in self.candidates if c.candidate_id in evidence
                      and any(not ev.verifies(i) and i.origin != "signal" for i in evidence[c.candidate_id].items)}
        self.spoken = {key for key in self.named if any(i.origin == "transcript" and not ev.verifies(i) and i.text.strip()
                                                        for i in evidence[key].items)}
        self.content_terms = {key: evidence[key].terms(verifying_only=True) for key in self.named}
        self.content_loose = {key: ev.loose(found) for key, found in self.content_terms.items()}
        self.unspoken_terms = {key: _terms_without_speech(evidence[key]) for key in self.spoken}
        self.unspoken_loose = {key: ev.loose(found) for key, found in self.unspoken_terms.items()}
        self.labels = {c.candidate_id: evidence[c.candidate_id].labels() if c.candidate_id in evidence else {} for c in self.candidates}
        frequency: dict[str, int] = {}
        for key, found in self.terms.items():
            for term in {*found, *self.loose[key]}:
                frequency[term] = frequency.get(term, 0) + 1
        count = max(1, len(self.candidates))
        self.idf = {term: math.log(1 + count / (1 + seen)) for term, seen in frequency.items()}
        # Words most of the footage is described with (the product's name, the film's style) tell no shot from
        # another: a sentence made of them alone matches nothing in particular, they still count in its length.
        # Title, end and subscribe screens: they show words, not the thing a sentence speaks of
        self.card = {c.candidate_id: _text_card(evidence.get(c.candidate_id)) for c in self.candidates}
        described = sum(1 for found in self.terms.values() if found)
        self.common = frozenset(term for term, seen in frequency.items()
                                if described >= COMMON_MIN_SHOTS and seen * 1000 > COMMON_SHARE * described)
        self.default_idf = math.log(1 + count)
        self.content = any(evidence[c.candidate_id].content for c in self.candidates if c.candidate_id in evidence)
        # proper names across languages (Kavkaz / Caucasus / Кавказ): the built-in table and the user's own aliases
        notes = [i.text for c in self.candidates if c.candidate_id in evidence for i in evidence[c.candidate_id].items
                 if i.origin == "user"]
        self.names = names.BUILTIN.with_notes(notes)
        self._mention_cache: dict[str, list[names.Mention]] = {}
        self.entities: dict[str, dict[str, list[tuple[names.Mention, int]]]] = {}
        seen_with: dict[str, int] = {}
        # the names a shot's verifying evidence alone shows (#836: a name said over a shot is not on it), and without
        # the speech, for the same candidates as above
        self.content_entities: dict[str, dict[str, list[tuple[names.Mention, int]]]] = {}
        self.unspoken_entities: dict[str, dict[str, list[tuple[names.Mention, int]]]] = {}
        for c in self.candidates:
            info = evidence.get(c.candidate_id)
            items = [i for i in (info.items if info else ()) if i.origin != "signal" and i.text]
            found = names.found_in(_texts(items), self.names)
            self.entities[c.candidate_id] = found
            if c.candidate_id in self.named:
                self.content_entities[c.candidate_id] = names.found_in(_texts([i for i in items if ev.verifies(i)]), self.names)
            if c.candidate_id in self.spoken:
                self.unspoken_entities[c.candidate_id] = names.found_in(
                    _texts([i for i in items if not (i.origin == "transcript" and not ev.verifies(i))]), self.names)
            for entity_id in found:
                seen_with[entity_id] = seen_with.get(entity_id, 0) + 1
        self.entity_idf = {e: math.log(1 + count / (1 + n)) for e, n in seen_with.items()}
        self.common_entities = frozenset(e for e, n in seen_with.items()
                                         if described >= COMMON_MIN_SHOTS and n * 1000 > COMMON_SHARE * described)

    def content_relevance(self, unit: Unit, candidate: Candidate, relevance: int) -> int:
        """The relevance the evidence that can verify the picture alone gives (no file name, no speech over the shot,
        #836), at most ``relevance``: what the confidence of a choice is built on."""
        key = candidate.candidate_id
        if key not in self.named or relevance <= 0:
            return relevance
        return min(relevance, self._relevance(unit, self.content_terms[key], self.content_loose[key],
                                              self.content_entities[key])[0])

    def unspoken_relevance(self, unit: Unit, candidate: Candidate, relevance: int) -> int:
        """The relevance without the speech over the shot (its file name kept), at most ``relevance`` (#836): a
        shot that matches a sentence only through what is said over it does not illustrate the sentence."""
        key = candidate.candidate_id
        if key not in self.spoken or relevance <= 0:
            return relevance
        return min(relevance, self._relevance(unit, self.unspoken_terms[key], self.unspoken_loose[key],
                                              self.unspoken_entities[key])[0])

    def relevance(self, unit: Unit, candidate: Candidate) -> tuple[int, list[str]]:
        """0–1000 and the matched words (for the reason)."""
        key = candidate.candidate_id
        return self._relevance(unit, self.terms[key], self.loose[key], self.entities.get(key, {}))

    def _relevance(self, unit: Unit, found: Mapping[str, int], loose: Mapping[str, int],
                   shot_names: Mapping[str, list[tuple[names.Mention, int]]]) -> tuple[int, list[str]]:
        wanted = ev.terms(unit.text)
        mentions = self._mentions(unit.text)
        if mentions:
            # a name is matched as the entity it names, in whatever language the footage says it: its words are not
            # matched again one by one (nor in pairs with the words around them)
            name_words = {w for m in mentions for w in m.words}
            wanted = frozenset(t for t in wanted if not any(names.fold(w.removeprefix(ev.LOOSE)) in name_words
                                                            for w in t.split()))
        if not wanted and not mentions:
            return 0, []
        matched, total, words = 0.0, 0.0, []
        for mention in {m.entity_id: m for m in mentions}.values():
            weight = 2 * self.entity_idf.get(mention.entity_id, self.default_idf)
            total += weight
            if mention.entity_id in self.common_entities:
                continue
            value, form = 0, ""
            for shot_mention, confidence in shot_names.get(mention.entity_id, ()):
                sure = names.tier(mention, shot_mention, self.names) * confidence // 1000
                if sure > value:
                    value, form = sure, shot_mention.written
            if value:
                matched += weight * value / 1000
                words.append(mention.written if names.fold(form) == names.fold(mention.written) else f"{mention.written} = {form}")
        for term in wanted:
            weight = self.idf.get(term, self.default_idf) * (2 if " " in term else 1)
            total += weight
            value = 0 if self._common(term) else ev.match(term, found, loose)
            if value:
                matched += weight * value / 1000
                words.append(term.removeprefix(ev.LOOSE))
        if not matched:
            return 0, []
        value = min(1.0, matched / max(1.5, 0.45 * total))
        words.sort(key=lambda w: (-("=" in w), -(" " in w), -self.idf.get(w, 0), w))
        return round(value * 1000), words[:4]

    def _mentions(self, text: str) -> list[names.Mention]:
        cached = self._mention_cache.get(text)
        if cached is None:
            cached = self._mention_cache[text] = self.names.mentions(text)
        return cached

    def _common(self, term: str) -> bool:
        if term in self.common:
            return True
        folded = term if term.startswith(ev.LOOSE) else ev.LOOSE + ev.fold(term)
        return folded in self.common

    def fit(self, unit: Unit, candidate: Candidate) -> tuple[int, str | None]:
        table = FIT.get(unit.function, {})
        best, label = 0, None
        for name, confidence in self.labels[candidate.candidate_id].items():
            value = table.get(name, 0) * confidence // 1000
            if value > best:
                best, label = value, name
        return best, label


_CARD_CUES = ("man hinh chu", "dong chu", "the tieu de", "man hinh tieu de", "chu chay", "chu ket", "chu tieu de", "credit",
              "dang ky kenh", "cam on da xem", "end card", "title card", "subscribe")
_TEXT_TOPIC = re.compile(r"\d|\b(gia|chu|tieu de|ten|khuyen mai|giam|ma giam|freeship|dong chu)\b")


# What a shot shows besides words: a frame labelled with one of these is footage that carries text (burned-in
# subtitles, a caption, a screen being used), not a screen of text. Gate S real set: every shot of a Chinese drama
# with subtitles was labelled text_on_screen and so held back as a card from every sentence not about words.
SCENE_LABELS = frozenset(ev.PRODUCT_LABELS) | {"action", "reaction", "establishing", "people", "object", "explanation"}


def _text_card(found: ev.Evidence | None) -> bool:
    """Whether what is known of a shot says it is a screen of text (a title, an end or a subscribe card)."""
    if found is None:
        return False
    labels = found.labels()
    if (labels.get("text_on_screen", 0) >= 400 and not found.product_visible
            and not any(labels.get(name, 0) >= 400 for name in SCENE_LABELS)):
        return True
    text = " ".join(ev.fold(item.text) for item in found.items if item.origin != "signal")
    return any(f" {cue} " in f" {text} " for cue in _CARD_CUES)


def about_text(unit: Unit) -> bool:
    """Whether a sentence speaks of something written (a price, a code, a title): a text card can show it. A number
    spelled in words is a number too ("một nghìn năm trăm dặm", #802: the map that shows "1500 MILES" was held back
    as a card from the sentence that names it), when the sentence speaks of something displayed (a map, a line of text):
    a spoken "ba mươi giây" in an opening names no frame to show, as before."""
    if _TEXT_TOPIC.search(ev.fold(ev.numbers(unit.text))):
        return True
    stated = said.of(unit.text)
    return stated.display and stated.any


def _length_fit(candidate_ms: int, wanted_ms: int) -> int:
    return 1000 if candidate_ms >= wanted_ms else candidate_ms * 1000 // max(1, wanted_ms)


ACCEPT_SHARE = 500  # a shot matching a sentence at least this share (‰) of its best match illustrates it
GOOD_COVERAGE = 900  # ‰ of a sentence's time on footage that illustrates it before it needs no review
LOSS_PENALTY = 450  # forward only: taking a shot that removes the only footage of a sentence still to come
HELD_PENALTY = 2000  # forward only: neutral footage that leaves the sentences short of footage; a held frame blocks export
MATCH_FULL = 500  # relevance at which a match counts as complete for the confidence
VERIFIED_BONUS = 150  # a shot whose evidence shows a number, title or stated words of the sentence (#802)
# ... and twice that for a sentence about something displayed (a map, a card, a line of text): there the shown fact is
# what the sentence is about, worth as much as a text card is worth against a sentence not about words (CARD_PENALTY)
DISPLAYED_BONUS = 300
CONTRADICTED_PENALTY = 300  # a shot showing another amount of the same kind (500 000 square miles for 1 500 miles)
PIECE_FLOOR = SHORT_FLOOR  # ms a new piece takes at least when the sentence's rest is shorter (others give way)
SKIP_PENALTY = 200  # forward only: passing over all the footage there is to spare (half for footage that illustrates)


def _coverage_note(milli: int, has_match: bool) -> str:
    if not has_match:
        return "không có cảnh nào khớp lời câu này: dùng cảnh chung, cần kiểm tra"
    if not milli:
        return "có cảnh khớp lời câu này nhưng không đặt được vào câu (cảnh quá ngắn hoặc cần cho câu sau): dùng cảnh chung, cần kiểm tra"
    if milli < GOOD_COVERAGE:
        return f"chỉ {milli // 10} % thời lượng câu có cảnh khớp lời (nguồn không đủ cảnh), phần còn lại dùng cảnh chung"
    return ""


@dataclass
class Exclusion:
    """What the user's exclusions do to one pool of candidates (#805)."""

    gone: set[str]                    # left out
    by: dict[str, str]                # candidate id → the exclusion (an id, or a source id) that leaves it out
    widened: dict[str, str]           # an unsplit shot left out for a part of it the user named (P2-N22) → that part
    reused: list[str]                 # excluded footage used anyway: nothing else was left (a conflict, never silent)
    reused_by: dict[str, str] = field(default_factory=dict)  # each of ``reused`` → the exclusion that names it


Span = tuple[str, int, int]


def _within(candidate: Candidate, spans: Sequence[Span] | None) -> bool:
    """Whether at least half of ``candidate`` lies in one of the recorded footage ``spans`` (same source); always
    when nothing was recorded (a review saved before #805 names ids only)."""
    if spans is None:
        return True
    for source, start, end in spans:
        inside = min(end, candidate.end_ms) - max(start, candidate.start_ms)
        if candidate.source_id == source and inside > 0 and inside * 2 >= candidate.length_ms:
            return True
    return False


def _overlaps(candidate: Candidate, spans: Sequence[Span] | None) -> bool:
    """Whether ``candidate`` shares any footage with ``spans`` (always, when nothing was recorded)."""
    if spans is None:
        return True
    return any(candidate.source_id == source and min(end, candidate.end_ms) > max(start, candidate.start_ms)
               for source, start, end in spans)


SLIVER_MS = 250  # less of an excluded span than this at a piece's edge is a boundary, not footage shown


def _shows(candidate: Candidate, spans: Sequence[Span]) -> bool:
    """Whether ``candidate`` shows a noticeable part of the recorded footage ``spans`` (more than a sliver at a cut
    boundary, or all of a short span)."""
    for source, start, end in spans:
        inside = min(end, candidate.end_ms) - max(start, candidate.start_ms)
        if candidate.source_id == source and inside > 0 and (inside >= SLIVER_MS or inside >= end - start):
            return True
    return False


def ranges_digest_input(edits: Edits) -> dict[str, Any]:
    """The recorded footage of the exclusions, for a plan's identity (empty when there is none, so a plan without it
    keeps the identity it had): the same ids on other footage are another plan."""
    kept = {i: [list(span) for span in spans] for i, spans in sorted(edits.exclude_ranges.items()) if i in edits.exclude}
    return {"exclude_ranges": kept} if kept else {}


def usable_pool(edits: Edits | None, pool: Sequence[Candidate], *, again: Iterable[str] = ()) -> list[Candidate]:
    """``pool`` without what the user's exclusions leave out: what a targeted look or extra frames may spend on.
    ``again`` are shots a decision names a part of: they stay even when left out whole for now, so they can be split
    again and the exclusion narrows back to the part the user named (P2-N22)."""
    if edits is None:
        return list(pool)
    gone = left_out(edits, pool) - set(again)
    return [c for c in pool if c.candidate_id not in gone]


def exclusion(edits: Edits, candidates: Sequence[Candidate]) -> Exclusion:
    """The candidates the user's exclusions leave out.

    * a shot or a source named (where an exclusion recorded its footage, only while the id still names some of it);
    * every piece of a whole shot named (``v1c3`` leaves out ``v1c3f1``, ``v1c3f2``…) (#805);
    * what lies within the footage an exclusion was made on (half a candidate or more), whatever it is called now: a
      new split or a new trim gives the same footage other ids (#805);
    * a whole shot when a part of it is named (``v1c3f2``) and the shot is not split this time, so the excluded
      footage is never shown (P2-N22); likewise a piece of a new split that shows a noticeable part of recorded
      footage without being mostly in it (``widened``, so the plan and the panel say so).

    When that leaves no footage at all, the shot of the last rule plays again, and when even what the user named
    covers everything, nothing is left out: ``reused`` lists that footage and the plan says so (a conflict for the
    user to settle, never a silent reuse)."""
    excluded = set(edits.exclude)
    recorded = {i: tuple(spans) for i, spans in edits.exclude_ranges.items() if i in excluded}
    spans = sorted(recorded.items())
    by: dict[str, str] = {}
    for c in candidates:
        cid = c.candidate_id
        # an id names footage only where it still is mostly that footage: a shot renumbered onto other footage is not it
        if cid in excluded and _within(c, recorded.get(cid)):
            by[cid] = cid
        elif parent(cid) in excluded and _within(c, recorded.get(parent(cid))):
            by[cid] = parent(cid)
        elif c.source_id in edits.exclude_sources:
            by[cid] = c.source_id
        else:
            reason = next((i for i, mine in spans if _within(c, mine)), None)
            if reason is not None:
                by[cid] = reason
    widened = {c.candidate_id: piece for piece in sorted(excluded) for c in candidates
               if piece != parent(piece) and c.candidate_id == parent(piece) and c.candidate_id not in by
               and _overlaps(c, recorded.get(piece))}
    # footage an exclusion recorded that a new split cuts across, so that no piece is mostly in it: each piece that
    # shows a noticeable part of it is left out too, reported as widened (the excluded footage is never shown, and
    # the user sees how much more went)
    for c in candidates:
        if c.candidate_id in by or c.candidate_id in widened:
            continue
        reason = next((i for i, mine in spans if _shows(c, mine)), None)
        if reason is not None:
            widened[c.candidate_id] = reason
    named = set(by)
    if len(named | set(widened)) < len(candidates):
        return Exclusion(named | set(widened), {**by, **widened}, widened, [])
    if len(named) < len(candidates):
        return Exclusion(named, by, {}, sorted(widened), dict(sorted(widened.items())))
    return Exclusion(set(), {}, {}, sorted(named), dict(sorted(by.items())))


def exclusion_log(left: Exclusion | None) -> dict[str, Any]:
    """What the user's exclusions left out of this pool, as the decision log records it (#805): each candidate left
    out with the exclusion that did it, the shots left out whole for a part of them, and footage used anyway."""
    if left is None or not (left.by or left.reused):
        return {}
    return {"exclusions": {"left_out": dict(sorted(left.by.items())), "widened": dict(sorted(left.widened.items())),
                           "reused": list(left.reused)}}


def left_out(edits: Edits, candidates: Sequence[Candidate]) -> set[str]:
    """The candidates the user's exclusions leave out (see :func:`exclusion`)."""
    return exclusion(edits, candidates).gone


def assign(found_units: Sequence[Unit], candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence], pacing: Pacing,
           *, forward_only: bool, edits: Edits | None = None, groups: Mapping[str, str] | None = None,
           stills: Iterable[str] = (), shown: Iterable[str] = (), product: bool = False) -> tuple[list[Choice], list[Decision], str]:
    """See :func:`_assign`. Approvals (``edits.keep``) are constraints only where they are needed: when the plan made
    without them already shows every approved sentence as approved, that plan is the plan, so approving alone never
    changes another sentence's footage (P2-N5). Only an approval whose footage the plan would change makes the
    approved sentences go first and hold their shots."""
    edits = edits or Edits()
    texts = {u.unit_id: u.text for u in found_units}
    keep = {unit_id: pieces for unit_id, pieces in edits.keep.items() if unit_id in texts and unit_id not in edits.pin
            and (unit_id not in edits.keep_words or edits.keep_words[unit_id] == words_digest(unit_id, texts[unit_id]))}
    kwargs = dict(forward_only=forward_only, groups=groups, stills=tuple(stills), shown=tuple(shown), product=product)
    free = Edits(dict(edits.pin), set(edits.exclude), set(edits.exclude_sources), exclude_ranges=dict(edits.exclude_ranges))
    if not keep:
        return _assign(found_units, candidates, evidence, pacing, edits=free, **kwargs)
    plain = _assign(found_units, candidates, evidence, pacing, edits=free, **kwargs)
    decided = {d.unit.unit_id: d for d in plain[1]}

    def shown_as_approved(unit_id: str, pieces: Sequence[tuple[str, int, int, bool]]) -> bool:
        decision = decided.get(unit_id)
        if decision is None:
            return False
        if any(offset < 0 for _, offset, _, _ in pieces):  # an approval from before the pieces were logged: its shots
            return list(dict.fromkeys(decision.chosen)) == list(dict.fromkeys(p[0] for p in pieces))
        return [(p["id"], p["offset_ms"], p["length_ms"], bool(p.get("repeat"))) for p in decision.pieces] \
            == [(cid, offset, length, bool(again)) for cid, offset, length, again in pieces]
    if all(shown_as_approved(unit_id, pieces) for unit_id, pieces in keep.items()):
        return plain
    held = Edits(dict(edits.pin), set(edits.exclude), set(edits.exclude_sources), keep, exclude_ranges=dict(edits.exclude_ranges))
    return _assign(found_units, candidates, evidence, pacing, edits=held, **kwargs)


def _assign(found_units: Sequence[Unit], candidates: Sequence[Candidate], evidence: Mapping[str, ev.Evidence], pacing: Pacing,
            *, forward_only: bool, edits: Edits | None = None, groups: Mapping[str, str] | None = None,
            stills: Iterable[str] = (), shown: Iterable[str] = (), product: bool = False) -> tuple[list[Choice], list[Decision], str]:
    """Footage for every unit, the decision log and the mode (``content`` or ``signals_only``).

    *Sentence coverage*: each sentence gets pieces until its whole time is covered, first from the footage that
    illustrates it (a match of at least half its best match), short shots included, then — only when the sources
    have no more — from neutral footage, and the decision records how much of the sentence is illustrated
    (``coverage``) and asks for review below 90 %.

    *Global allocation*: with reordering allowed, sentences whose illustrating footage is scarcest are filled
    first and generic sentences (a hook or a call to action without a match) last, so an opening never takes the
    only shot of a later detail. Forward only (the source keeps its order), sentences are filled in order and a
    shot is weighed by the illustrating footage it would put behind the sentences still to come.

    ``stills`` are candidate ids of photos: they can be held as long as needed. ``groups`` maps a candidate to its
    duplicate group; ``shown`` lists footage another part of the same video showed. When a forward-only source has
    nothing left ahead, earlier footage is shown again, declared as a repeat and flagged.

    ``product``: the footage is of one product (Bán hàng): an opening with no match of its own is READY only on a
    shot the vision saw the product in (P1-N1).
    """
    edits = edits or Edits()
    groups = groups or {}
    still_ids = set(stills)
    gone = left_out(edits, candidates)
    pool = [c for c in candidates if c.candidate_id not in gone]
    by_id = {c.candidate_id: c for c in candidates}
    # the pins that still name a shot the plan may use: a pin whose shot is gone or left out is no choice of the
    # user's any more, so the sentence is the AI's again and every check of the AI's choice applies to it (#805)
    live_pins = {unit_id: [i for i in ids if i in by_id and i not in gone] for unit_id, ids in edits.pin.items()}
    if not pool:
        raise ValueError("no footage left after the edits")
    scorer = Scorer(pool, evidence)
    mode = "content" if scorer.content else "signals_only"
    # footage the user approved is usable for that sentence, whatever its measurements (a dark clip approved before a
    # well-exposed one was added: the approval holds, never a crash)
    approved_ids = {piece[0] for pieces in edits.keep.values() for piece in pieces}
    sound = {c.candidate_id for c in ([c for c in pool if c.quality.usable] or pool)} | approved_ids
    usable = [c for c in pool if c.candidate_id in sound]
    uses: dict[str, int] = {}
    group_uses: dict[str, int] = {}
    for shown_id in shown:  # footage another part of the same video already showed: weighed as a duplicate
        group = groups.get(shown_id, shown_id)
        group_uses[group] = group_uses.get(group, 0) + 1
    resume: dict[str, int] = {}
    source_end: dict[str, int] = {}
    relevance_of = {(u.unit_id, c.candidate_id): scorer.relevance(u, c) for u in found_units for c in usable}
    # a text card illustrates only a sentence about what is written; while the source has any other usable footage
    # the words printed on a card (a title that repeats the opening: dev2-title-card-opening) are no match for a
    # sentence that is not about them, so it is filled from real footage and flagged when nothing else matches.
    # A source of nothing but cards keeps them (dev2-hook-text-card).
    # A sentence whose words were found only on such a card is not generic: what it says is written somewhere, and
    # the real footage it gets instead is looked at (a call to action naming an offer the card shows).
    on_card: set[str] = set()
    if any(not scorer.card[c.candidate_id] for c in usable):
        for u in found_units:
            if not about_text(u):
                for c in usable:
                    if scorer.card[c.candidate_id]:
                        if relevance_of[(u.unit_id, c.candidate_id)][0] >= MIN_RELEVANCE:
                            on_card.add(u.unit_id)
                        relevance_of[(u.unit_id, c.candidate_id)] = (0, [])
    best_of = {u.unit_id: max((relevance_of[(u.unit_id, c.candidate_id)][0] for c in usable), default=0) for u in found_units}
    accepts: dict[str, set[str]] = {}
    for u in found_units:
        best = best_of[u.unit_id]
        accepts[u.unit_id] = {c.candidate_id for c in usable if best >= MIN_RELEVANCE
                              and relevance_of[(u.unit_id, c.candidate_id)][0] >= max(MIN_RELEVANCE, ACCEPT_SHARE * best // 1000)}
    # a shot belongs to the sentence it is clearly about: one that another sentence matches at twice the score
    # (and as one of its own) does not illustrate this one ("nhạc" in an opening does not make the shot of the
    # earbud touch that stops the music an opening shot)
    # ... and is not taken from a sentence for which it is as good a match as that sentence has: a sentence made of
    # strong words (an opening naming the product) outscores a plain one on every shot, so the claim also needs the
    # shot to be relatively closer to the other sentence's best than to this one's (dev2-blower-name-claim).
    def strength(unit_id: str, cid: str) -> int:
        return relevance_of[(unit_id, cid)][0] * 1000 // max(1, best_of[unit_id])

    for u in found_units:
        mine = relevance_of
        accepts[u.unit_id] = {cid for cid in accepts[u.unit_id]
                              if not any(cid in accepts[v.unit_id] and mine[(v.unit_id, cid)][0] >= 2 * mine[(u.unit_id, cid)][0]
                                         and strength(v.unit_id, cid) > strength(u.unit_id, cid)
                                         for v in found_units if v.unit_id != u.unit_id)}
    # what a sentence says is displayed (a number with its unit, a title, what a card states) is a fact the shot must
    # show, not a kind of picture (#802): a shot whose evidence shows it illustrates the sentence whatever other words
    # it shares, and one showing another amount of the same kind does not, however many words it shares ("bản đồ",
    # "đánh dấu", "dặm" on a map of 500 000 square miles for "1 500 dặm")
    stated = {u.unit_id: said.of(u.text) for u in found_units}
    shown_text = {c.candidate_id: said.content_text(evidence.get(c.candidate_id)) for c in usable}
    checked: dict[tuple[str, str], said.Check] = {}
    for u in found_units:
        if stated[u.unit_id].any and mode == "content":
            for c in usable:
                result = checked[(u.unit_id, c.candidate_id)] = said.check(stated[u.unit_id], shown_text[c.candidate_id])
                if result.conflicts and stated[u.unit_id].display:
                    accepts[u.unit_id].discard(c.candidate_id)
                elif result.verified and not (scorer.card[c.candidate_id] and not about_text(u)):
                    accepts[u.unit_id].add(c.candidate_id)
    units_by_id = {u.unit_id: u for u in found_units}
    positions = {u.unit_id: i for i, u in enumerate(found_units)}
    # check A (hook stealing): the shot a later sentence is about is that sentence's, not the opening's: an opening
    # that promises the result ("smooth in thirty seconds") would otherwise show the result shot before the sentence
    # that presents it. An opening or closing keeps only what no later sentence describing something accepts.
    def owners_after(unit: Unit, cid: str) -> set[str]:
        return {v.unit_id for v in found_units if positions[v.unit_id] > positions[unit.unit_id]
                and v.function not in OPENING_CLOSING and cid in accepts[v.unit_id]}

    for u in found_units:
        if u.function in OPENING_CLOSING and not live_pins.get(u.unit_id):
            accepts[u.unit_id] = {cid for cid in accepts[u.unit_id] if not owners_after(u, cid)}
    accepted_by: dict[str, set[str]] = {}
    for unit_id, ids in accepts.items():
        for candidate_id in ids:
            accepted_by.setdefault(candidate_id, set()).add(unit_id)
    # approved footage (P2-N5): a sentence the user approved keeps its shots, and no other sentence takes them. A pin
    # is the newer, explicit choice: a shot pinned to one sentence is no longer reserved for another that approved it
    # (that approval then fails, with its reason, when the plan is checked against the decisions).
    pinned_ids = {cid for ids in edits.pin.values() for cid in ids}
    pool_ids = {c.candidate_id for c in pool}
    kept: dict[str, list[tuple[str, int, int, bool]]] = {}
    reserved: dict[str, set[str]] = {}
    for unit_id, pieces in sorted(edits.keep.items()):
        if unit_id in edits.pin or unit_id not in {u.unit_id for u in found_units}:
            continue
        mine_kept = [piece for piece in pieces if piece[0] in pool_ids and piece[0] not in pinned_ids]
        if mine_kept:
            kept[unit_id] = mine_kept
            for piece in mine_kept:
                reserved.setdefault(piece[0], set()).add(unit_id)

    def remaining(candidate: Candidate) -> tuple[int, int]:
        if candidate.candidate_id in still_ids:
            return 0, 10**9
        start = resume.get(candidate.candidate_id, candidate.start_ms)
        return start, candidate.end_ms - start

    def allowed(candidate: Candidate) -> bool:
        if not forward_only or candidate.candidate_id in still_ids:
            return True
        return candidate.start_ms >= source_end.get(candidate.source_id, 0) or (
            candidate.candidate_id in resume and resume[candidate.candidate_id] < candidate.end_ms
            and resume[candidate.candidate_id] >= source_end.get(candidate.source_id, 0))

    def ahead(candidate: Candidate, ids: Iterable[str] | None = None) -> int:
        """Footage still playable after playing ``candidate`` (forward only), optionally only among ``ids``."""
        total = 0
        for other in usable if ids is None else (by_id[i] for i in ids if i in by_id):
            if other.candidate_id in still_ids:
                return 10**9
            if not allowed(other) or other.candidate_id == candidate.candidate_id:
                continue
            if other.source_id == candidate.source_id and other.start_ms < candidate.end_ms:
                continue
            total += remaining(other)[1]
        return total

    def supply(unit_id: str) -> int:
        """Illustrating footage of ``unit_id`` still playable now."""
        return sum(remaining(by_id[i])[1] for i in accepts[unit_id] if allowed(by_id[i]) and remaining(by_id[i])[1] > 0)

    def lost(candidate: Candidate, length: int, later: Sequence[Unit]) -> int:
        """Forward only: ms of their own footage the next sentences lose if ``length`` of ``candidate`` plays now."""
        if not forward_only or candidate.candidate_id in still_ids:
            return 0
        left = remaining(candidate)[1]
        total = 0
        for later_unit in later[:6]:
            ids = accepts[later_unit.unit_id]
            if not ids:
                continue
            before = min(later_unit.length_ms, supply(later_unit.unit_id))
            if before <= 0:
                continue
            kept = ahead(candidate, ids) + (max(0, left - length) if candidate.candidate_id in ids else 0)
            total += max(0, before - min(later_unit.length_ms, kept))
        return total

    def starved(unit: Unit, candidate: Candidate, length: int) -> int:
        """Reordering: ms of their own footage the sentences not filled yet lose if ``unit`` plays ``length`` of
        ``candidate`` now."""
        total = 0
        for other_id in accepted_by.get(candidate.candidate_id, ()):
            if other_id == unit.unit_id or other_id in decisions:
                continue
            other = units_by_id[other_id]
            have = supply(other_id)
            total += max(0, min(other.length_ms, have) - min(other.length_ms, have - length))
        return total

    def confidence_of(unit: Unit, candidate: Candidate, relevance: int, fit: int) -> int:
        info = evidence.get(candidate.candidate_id)
        if mode != "content":
            return min(SIGNALS_ONLY_CAP, 250 + candidate.quality.score // 5)
        # only what can verify the picture is trusted (#836): the speech over a shot finds and ranks it, it never
        # vouches for it, so a shot known from its speech alone is never READY on it
        trusted = _trusted(info)
        relevance = scorer.content_relevance(unit, candidate, relevance)
        claim = checked.get((unit.unit_id, candidate.candidate_id))
        # a shot whose evidence shows every number, title or stated words the sentence says is displayed matches it
        # fully, however few other words they share (the globe with "1500 MILES"); still bounded by its trust
        proven = claim is not None and stated[unit.unit_id].display and bool(claim.verified) \
            and not claim.unverified and not claim.conflicts
        if relevance <= 0 and not proven:  # no word of the sentence in what is known of the shot: role fit at most
            return min(SIGNALS_ONLY_CAP, 250 + fit // 4)
        match = 1000 if proven else min(1000, relevance * 1000 // MATCH_FULL)
        return trusted * (600 + 400 * match // 1000) // 1000

    def spoken_only(unit: Unit, candidate: Candidate) -> bool:
        """Whether ``candidate`` matches ``unit`` only through the speech over it (#836): without the speech its
        evidence shares no word with the sentence and shows none of what the sentence says is displayed."""
        if candidate.candidate_id not in scorer.spoken:
            return False
        claim = checked.get((unit.unit_id, candidate.candidate_id))
        if claim is not None and claim.verified:
            return False
        relevance = relevance_of[(unit.unit_id, candidate.candidate_id)][0]
        return scorer.unspoken_relevance(unit, candidate, relevance) < MIN_RELEVANCE

    ahead_now = 0
    narration_left = 0

    def score(unit: Unit, candidate: Candidate, wanted: int, best: int, later: Sequence[Unit], tier: str) -> tuple[int, int, list[str]]:
        relevance, matched = relevance_of[(unit.unit_id, candidate.candidate_id)]
        fit, label = scorer.fit(unit, candidate)
        quality = candidate.quality.score
        _, left = remaining(candidate)
        # the closest match gets the full weight, so a sharp but unrelated shot cannot outscore the shot of the thing
        strength = relevance * 1000 // best if best >= MIN_RELEVANCE else relevance
        total = (450 * strength + 250 * fit + 150 * quality + 50 * _length_fit(left, min(wanted, pacing.target))) // 1000
        info = evidence.get(candidate.candidate_id)
        card = scorer.card[candidate.candidate_id] and not about_text(unit)
        if card:
            total -= CARD_PENALTY  # moving letters are not an opening, and not what the sentence shows
        claim = checked.get((unit.unit_id, candidate.candidate_id))
        if claim is not None:
            if claim.verified:
                total += DISPLAYED_BONUS if stated[unit.unit_id].display else VERIFIED_BONUS
            if claim.conflicts and stated[unit.unit_id].display:
                total -= CONTRADICTED_PENALTY
        if unit.function == "hook" and not card:
            total += min(60, candidate.motion // 4) + (info.hook // 10 if info else 0)
        elif unit.function == "narration":
            total += min(30, candidate.motion // 8)
        continuing = candidate.candidate_id in resume and resume[candidate.candidate_id] < candidate.end_ms
        if continuing:
            total += 40  # playing on keeps the action whole
        else:
            total -= REUSE_PENALTY * uses.get(candidate.candidate_id, 0)
        total -= DUPLICATE_PENALTY * (group_uses.get(groups.get(candidate.candidate_id, candidate.candidate_id), 0)
                                      - uses.get(candidate.candidate_id, 0))
        if tier == "neutral" and (accepted_by.get(candidate.candidate_id, set()) - set(decisions)
                                  or (unit.function in OPENING_CLOSING and owners_after(unit, candidate.candidate_id))):
            total -= 200  # another sentence's footage says something else: neutral footage first
        if forward_only and candidate.candidate_id not in still_ids:
            # passing over footage loses it for good: the nearest fitting footage first, more so when it says nothing
            skipped = max(0, remaining(candidate)[0] - source_end.get(candidate.source_id, 0))
            if skipped:
                # weighed against the footage to spare: cheap early in a long source, costly once it runs short
                spare = max(1, ahead_now - narration_left)
                weight = SKIP_PENALTY if tier != "match" else SKIP_PENALTY // 2
                total -= min(weight, weight * skipped // spare)
        if forward_only and later and candidate.candidate_id not in still_ids:
            # taking this shot puts footage behind: what the sentences still to come lose of their own footage
            total -= LOSS_PENALTY * min(2 * wanted, lost(candidate, min(left, wanted), later)) // max(1, wanted)
        elif not forward_only and candidate.candidate_id not in still_ids:
            # reordering: what the sentences still to fill lose of their own footage if this one takes it
            total -= LOSS_PENALTY * min(2 * wanted, starved(unit, candidate, min(left, wanted))) // max(1, wanted)
        reasons = []
        if matched:
            origins = info.origins() if info else []
            reasons.append(f"khớp lời “{', '.join(matched)}” ({', '.join(origins[:2]) or 'mô tả'})")
        if label:
            reasons.append(f"cảnh {ev.LABEL_NAMES_VI.get(label, label)} hợp câu {unit.function}")
        if claim is not None and claim.verified:
            reasons.append("cảnh có điều lời câu nói: " + ", ".join(claim.verified[:3]))
        if claim is not None and claim.conflicts and stated[unit.unit_id].display:
            reasons.append("cảnh hiển thị con số khác lời câu: " + ", ".join(claim.conflicts[:3]))
        if not candidate.quality.usable:
            reasons.append("chất lượng thấp: " + "; ".join(candidate.quality.problems))
        return total, max(0, min(1000, confidence_of(unit, candidate, relevance, fit))), reasons

    # order of filling: pinned first, then (reordering allowed) the scarcest illustrated sentences, generic ones last
    if forward_only:
        order = list(found_units)
    else:
        def scarcity(u: Unit) -> tuple:
            pinned_first = 0 if live_pins.get(u.unit_id) else (1 if u.unit_id in kept else 2)
            has = bool(accepts[u.unit_id])
            # an opening or a call to action chooses after the sentences that describe something: it can do with
            # any good shot, they need theirs (dev2-blower-name-claim, dev2-toothpick-function-words)
            framing = 1 if u.function in OPENING_CLOSING else 0
            return (pinned_first, 0 if has else 1, framing, supply(u.unit_id) * 1000 // max(1, u.length_ms) if has else 0,
                    positions[u.unit_id])
        order = sorted(found_units, key=scarcity)
    by_unit: dict[str, list[Choice]] = {}
    decisions: dict[str, Decision] = {}
    previous_source: dict[int, str] = {}

    for unit in order:
        position = positions[unit.unit_id]
        later = found_units[position + 1:] if forward_only else [u for u in order[order.index(unit) + 1:]]
        decision = Decision(unit)
        decisions[unit.unit_id] = decision
        mine: list[Choice] = []
        by_unit[unit.unit_id] = mine
        after = sum(u.length_ms for u in found_units[position + 1:]) if forward_only else 0
        need = unit.length_ms
        best = best_of[unit.unit_id]
        accept = accepts[unit.unit_id]
        illustrated = 0
        user_ms = 0
        spoken_ms = 0
        weak_spoken_ms = 0  # time of shots the speech over them matches while what is seen is too weak (#836)
        pinned = [by_id[i] for i in live_pins.get(unit.unit_id, [])]
        if len([i for i in edits.pin.get(unit.unit_id, []) if i in by_id]) != len(pinned):
            decision.notes.append("một cảnh đã ghim nằm trong cảnh bạn đã loại nên được bỏ qua")
        elif len(pinned) != len(edits.pin.get(unit.unit_id, [])):
            decision.notes.append("một số cảnh đã ghim không còn trong danh sách nên được bỏ qua")
        saved = (dict(resume), dict(source_end), dict(uses), dict(group_uses))
        picks: list[dict[str, Any]] = []
        covered = 0

        def free(candidate: Candidate, me: str = unit.unit_id) -> bool:
            """Not footage another sentence's approval keeps, nor (forward only) a shot that would play past it."""
            owners = reserved.get(candidate.candidate_id)
            if owners:
                return me in owners
            if forward_only:
                for cid, owners in reserved.items():
                    held = by_id[cid]
                    if owners - {me} - set(decisions) and held.source_id == candidate.source_id \
                            and held.candidate_id not in still_ids and candidate.end_ms > held.start_ms:
                        return False
            return True
        here = [c for c in usable if free(c)] or usable

        def last_source() -> str | None:
            if picks:
                return picks[-1]["candidate"].source_id
            earlier = by_unit.get(found_units[position - 1].unit_id) if position else None
            return earlier[-1].candidate.source_id if earlier else None

        def consume(candidate: Candidate, offset: int, length: int) -> None:
            uses[candidate.candidate_id] = uses.get(candidate.candidate_id, 0) + 1
            group = groups.get(candidate.candidate_id, candidate.candidate_id)
            group_uses[group] = group_uses.get(group, 0) + 1
            if candidate.candidate_id not in still_ids:
                resume[candidate.candidate_id] = offset + length
                source_end[candidate.source_id] = max(source_end.get(candidate.source_id, 0), offset + length)

        def record(candidate: Candidate, tier: str, cap: int, ranked: Sequence[tuple]) -> int:
            """Add (or extend) a piece of ``candidate``; returns the ms it adds."""
            nonlocal covered
            offset, left = remaining(candidate)
            repeat = tier == "repeat" or left <= 0 or (forward_only and candidate.candidate_id not in still_ids
                                                         and not allowed(candidate))
            if left <= 0:
                offset, left = candidate.start_ms, candidate.length_ms
            length = cap if candidate.candidate_id in still_ids else min(left, cap)
            if length <= 0:
                return 0
            if picks and picks[-1]["candidate"].candidate_id == candidate.candidate_id \
                    and picks[-1]["offset"] + picks[-1]["length"] == offset and not repeat:
                picks[-1]["length"] += length  # playing on: one piece
            else:
                total, confidence, reasons, _ = next(r for r in ranked if r[3] is candidate) if ranked else (1000, 1000, [], None)
                picks.append({"candidate": candidate, "offset": offset, "length": length, "tier": tier, "repeat": repeat,
                              "score": total, "confidence": confidence, "reasons": list(reasons)})
            consume(candidate, offset, length)
            covered += length
            return length

        def rank(pool: Sequence[Candidate], tier: str, wanted: int) -> list[tuple]:
            nonlocal ahead_now, narration_left
            if forward_only:
                ahead_now = sum(remaining(c)[1] for c in usable if allowed(c) and c.candidate_id not in still_ids)
                narration_left = after + need - covered
            ranked = []
            previous = last_source()
            for candidate in pool:
                total, confidence, reasons = score(unit, candidate, wanted, best, later, tier)
                if candidate.source_id == previous and len({c.source_id for c in usable}) > 1 \
                        and not (picks and picks[-1]["candidate"].candidate_id == candidate.candidate_id):
                    total -= SAME_SOURCE_PENALTY
                ranked.append((total, confidence, reasons, candidate))
            ranked.sort(key=lambda item: (-item[0], item[3].source_id, item[3].start_ms))
            if not decision.alternatives:
                # the next best of all the footage (the UI's "other shots"), not only of this round's pool
                seen = {r[3].candidate_id for r in ranked[:1]}
                others = [(score(unit, c, wanted, best, later, "match")[0], c) for c in usable
                          if allowed(c) and c.candidate_id not in seen and remaining(c)[1] > 0]
                others.sort(key=lambda item: (-item[0], item[1].source_id, item[1].start_ms))
                decision.alternatives = [{"candidate_id": c.candidate_id, "score": t,
                                          "reasons": list(score(unit, c, wanted, best, later, "match")[2])} for t, c in others[:3]]
            return ranked

        def tail(candidate: Candidate) -> bool:
            """The rest of a shot another sentence it illustrates has already shown."""
            return bool(uses.get(candidate.candidate_id) and accepted_by.get(candidate.candidate_id, set()) - {unit.unit_id})

        def safe(candidate: Candidate, later_need: int, wanted: int) -> bool:
            """Forward only: what stays ahead after it still covers the sentences to come."""
            return (not forward_only or not later_need or candidate.candidate_id in still_ids
                    or ahead(candidate) + max(0, remaining(candidate)[1] - wanted) >= later_need)

        def short_of(candidate: Candidate, later_need: int, wanted: int) -> int:
            """Forward only: footage the sentences to come and the rest of this one (the part the shot is too short
            for) will lack after it plays: frames held later."""
            if not forward_only or candidate.candidate_id in still_ids:
                return 0
            left = remaining(candidate)[1]
            return max(0, later_need + max(0, wanted - left) - ahead(candidate) - max(0, left - wanted))

        for candidate in pinned:  # the user's choice, in order
            if covered >= need:
                break
            record(candidate, "pinned", need - covered, [(1000, 1000, ["chọn bởi người dùng"], candidate)])
        for cid, offset, length, again in kept.get(unit.unit_id, []):  # what the user approved, as it played
            if covered >= need:
                break
            candidate = by_id[cid]
            still = cid in still_ids
            tier = "repeat" if again else ("match" if cid in accept else "neutral")
            total, confidence, reasons, _ = rank([candidate], tier, need - covered)[0]  # also the "other shots"
            reasons = [*reasons, "giữ cảnh bạn đã duyệt"]
            if offset < 0:  # an approval from before the pieces were logged: the shot, wherever it can play
                if allowed(candidate) and (still or remaining(candidate)[1] >= SHORT_FLOOR):
                    record(candidate, tier, need - covered, [(total, confidence, reasons, candidate)])
                continue
            if not still and (offset < candidate.start_ms or offset + length > candidate.end_ms):
                continue  # no longer playable as approved: the check of the decisions says why
            length = min(length, need - covered)
            # forward only, footage behind what has played is shown again: declared, as it was when approved
            repeat = again or (forward_only and not still and offset < source_end.get(candidate.source_id, 0))
            picks.append({"candidate": candidate, "offset": 0 if still else offset, "length": length, "tier": tier,
                          "repeat": repeat, "score": total, "confidence": confidence, "reasons": reasons, "kept": True})
            before = resume.get(cid)
            consume(candidate, 0 if still else offset, length)
            if not still and before is not None:
                resume[cid] = max(before, resume[cid])
            covered += length
        # 1. footage that illustrates the sentence, the closest first, short shots included
        while covered < need:
            rest = need - covered
            wanted = rest if rest < pacing.target + pacing.minimum else pacing.target
            later_need = after + max(0, rest - wanted)
            pool = [c for c in here if c.candidate_id in accept and allowed(c) and remaining(c)[1] >= SHORT_FLOOR
                    and safe(c, later_need, wanted)
                    # forward only: not when the sentences to come lose more of their own footage than this one gains
                    and lost(c, min(remaining(c)[1], wanted), later) <= min(remaining(c)[1], wanted)]
            if not forward_only:  # nor a short leftover of a shot another sentence already showed, while others remain
                pool = [c for c in pool if not (tail(c) and remaining(c)[1] < pacing.minimum)] or pool
            if not pool:
                break
            ranked = rank(pool, "match", wanted)
            # never a piece under the floor: a short rest takes a real piece and the others give way (step 4)
            if not record(ranked[0][3], "match", max(wanted, PIECE_FLOOR), ranked):
                break
        # 2. not enough: let the illustrating shots already chosen play longer (up to the longest pace)
        if covered < need:
            for pick in picks:
                if covered >= need or pick["tier"] not in ("match", "pinned") or pick["candidate"].candidate_id in still_ids:
                    continue
                candidate = pick["candidate"]
                if resume.get(candidate.candidate_id) != pick["offset"] + pick["length"]:
                    continue
                extra = min(need - covered, candidate.end_ms - resume[candidate.candidate_id], max(0, pacing.maximum - pick["length"]))
                if extra > 0 and (not forward_only or safe(candidate, after, 0)):
                    pick["length"] += extra
                    resume[candidate.candidate_id] += extra
                    source_end[candidate.source_id] = max(source_end.get(candidate.source_id, 0), resume[candidate.candidate_id])
                    covered += extra
        # 3. still not enough: neutral footage (said so), then, when every source has played out, a declared repeat
        while covered < need:
            rest = need - covered
            wanted = rest if rest < pacing.target + pacing.minimum else pacing.target
            later_need = after + max(0, rest - wanted)
            floor = max(min(pacing.minimum, rest), pacing.minimum // 2)  # filler is never a flash
            fits = [c for c in here if allowed(c) and remaining(c)[1] >= floor
                    # this sentence's own footage left out above for what it costs the next ones stays out
                    and not (c.candidate_id in accept and lost(c, min(remaining(c)[1], wanted), later) > min(remaining(c)[1], wanted))]
            pool = [c for c in fits if safe(c, later_need, wanted)]
            least = min((short_of(c, later_need, wanted) for c in pool), default=0)
            if forward_only and least >= SHORT_FLOOR:
                # every safe filler shot leaves at least a piece's worth of footage short (a held frame): shots shorter
                # than the filler floor that leave less short compete with them (rc13 W2 i1: u2 took a 1225 ms shot
                # past a 1138 ms one, ran out 1126 ms short and held a frame)
                pool = pool + [c for c in here if allowed(c) and SHORT_FLOOR <= remaining(c)[1] < floor
                               and not (c.candidate_id in accept and lost(c, min(remaining(c)[1], wanted), later) > min(remaining(c)[1], wanted))
                               and short_of(c, later_need, wanted) < least]
            if not pool and forward_only:
                # nothing ahead is safe for the sentences to come: whatever plays now costs them alike, so this
                # sentence's own footage goes before filler (rc4 Top 5: its one match was left out for safety, then
                # an equally unsafe neutral shot was shown and the sentence read "has a match… 0 %")
                own_left = [c for c in here if c.candidate_id in accept and allowed(c) and remaining(c)[1] >= SHORT_FLOOR
                            and lost(c, min(remaining(c)[1], wanted), later) <= min(remaining(c)[1], wanted)]
                if own_left:
                    ranked = rank(own_left, "match", wanted)
                    if record(ranked[0][3], "match", max(wanted, PIECE_FLOOR), ranked):
                        continue
            pool = pool or fits or [c for c in here if allowed(c) and remaining(c)[1] >= SHORT_FLOOR]
            # another sentence's own footage is not filler: rather than show what a sentence says elsewhere (the
            # keyboard shot behind the price), the sentence shows its own shot again, declared as a repeat
            # ... nor is the rest of a shot another sentence about it already showed (the tail of its key shot)
            own = [p["candidate"] for p in picks if p["tier"] in ("match", "pinned")]
            free = [c for c in pool if not accepted_by.get(c.candidate_id, set()) - {unit.unit_id}]
            if own and pool and not free and not forward_only:
                again = max(own, key=lambda c: (c.length_ms, c.candidate_id))
                if record(again, "repeat", max(wanted, pacing.minimum // 2), []):
                    continue
            if not forward_only and pool:
                untouched = [c for c in pool if not tail(c)]
                spare = [c for c in here if allowed(c) and not accepted_by.get(c.candidate_id, set()) - {unit.unit_id}]
                if not untouched and spare:  # every fresh shot left is such a tail: a neutral shot again, said so
                    again = max(spare, key=lambda c: (c.length_ms, c.candidate_id))
                    if record(again, "repeat", max(wanted, pacing.minimum // 2), []):
                        continue
                pool = untouched or pool
            if not pool and rest < SHORT_FLOOR and picks:
                picks[-1]["length"] += rest  # a last few frames: the shot before plays on rather than a flash
                covered += rest
                break
            tier = "neutral" if pool else "repeat"
            ranked = rank(pool or here, tier, wanted)
            if forward_only and pool and len(ranked) > 1:
                # weighed like a lost match: the frames a shot leaves to hold, this sentence's included
                ranked = sorted(((total - HELD_PENALTY * min(2 * wanted, short_of(candidate, later_need, wanted)) // max(1, wanted),
                                  confidence, reasons, candidate) for total, confidence, reasons, candidate in ranked),
                                key=lambda item: (-item[0], item[3].source_id, item[3].start_ms))
            if not record(ranked[0][3], tier, max(wanted, pacing.minimum // 2), ranked):
                break
        # 4. exactly the sentence's length, no piece under the floor: trim the pieces with the most slack
        excess = covered - need
        while excess > 0 and picks:
            def slack(pick: dict[str, Any]) -> int:
                floor = SHORT_FLOOR if pick["tier"] in ("match", "pinned") else pacing.minimum // 2
                return pick["length"] - floor
            options = sorted(picks, key=lambda p: (p["tier"] in ("match", "pinned"), -slack(p)))
            pick = next((p for p in options if slack(p) > 0), None)
            if pick is None:
                dropped = picks.pop()
                excess -= dropped["length"]
                continue
            cut = min(excess, slack(pick))
            pick["length"] -= cut
            excess -= cut
        if excess < 0 and picks:  # dropping a piece left a little: the last piece plays on if it can
            picks[-1]["length"] -= excess
        _settle(picks, need, here, still_ids, accept, used={c for c, n in saved[2].items() if n},
                forward_only=forward_only, source_end=saved[1],
                claimed={cid for v in found_units if v.unit_id not in decisions for cid in accepts[v.unit_id]})
        covered = sum(p["length"] for p in picks)
        # the state as the final pieces leave it
        resume.clear(); resume.update(saved[0])
        source_end.clear(); source_end.update(saved[1])
        uses.clear(); uses.update(saved[2])
        group_uses.clear(); group_uses.update(saved[3])
        for pick in picks:
            candidate = pick["candidate"]
            continuing = candidate.candidate_id in resume and 0 < resume[candidate.candidate_id] < candidate.end_ms
            reasons = pick["reasons"]
            confidence = pick["confidence"]
            if pick["repeat"] or (uses.get(candidate.candidate_id) and not continuing):
                reasons.append("nhắc lại cảnh đã chiếu (nguồn không còn cảnh phù hợp phía trước)")
                confidence = min(confidence, 550)
            if mode == "signals_only":
                reasons.append("chưa có bằng chứng nội dung: chọn theo điểm cắt, độ nét, đa dạng")
            if pick["tier"] in ("neutral", "repeat") and (accept or mode == "content"):
                reasons.append("cảnh chung: không còn cảnh khớp lời câu này")
                confidence = min(confidence, SIGNALS_ONLY_CAP)
            # a shot shown again to fill the sentence says nothing new: it is not counted as illustrating it
            # a pinned shot the AI saw no match in is the user's illustration, never AI evidence: it counts apart (rc9
            # P2-N14: a pin of an unrelated shot turned into "MATCH_AVAILABLE, coverage 1000, READY")
            if candidate.candidate_id in accept and pick["tier"] != "repeat" and spoken_only(unit, candidate):
                # a shot that matches the sentence only through the speech over it is not footage that illustrates
                # it (#836): the time counts apart and the sentence asks for a look (and a targeted vision look)
                spoken_ms += pick["length"]
            elif candidate.candidate_id in accept and pick["tier"] != "repeat":
                illustrated += pick["length"]
            elif pick["tier"] == "pinned":
                user_ms += pick["length"]
            needs_review = confidence < REVIEW_BELOW or not candidate.quality.usable
            if confidence < REVIEW_BELOW and candidate.candidate_id in accept and candidate.candidate_id in scorer.spoken \
                    and pick["tier"] != "repeat" and not spoken_only(unit, candidate):
                weak_spoken_ms += pick["length"]
            mine.append(Choice(unit.unit_id, candidate, pick["offset"], pick["length"], pick["score"], confidence, needs_review,
                               tuple(reasons[:8])))
            decision.chosen.append(candidate.candidate_id)
            decision.pieces.append({"id": candidate.candidate_id, "offset_ms": pick["offset"], "length_ms": pick["length"],
                                    **({"repeat": True} if pick["tier"] == "repeat" or (pick.get("kept") and pick["repeat"]) else {})})
            before = resume.get(candidate.candidate_id)
            consume(candidate, pick["offset"], pick["length"])
            if pick.get("kept") and before is not None and candidate.candidate_id not in still_ids:
                resume[candidate.candidate_id] = max(before, resume[candidate.candidate_id])
        milli = illustrated * 1000 // max(1, unit.length_ms)
        pinned_by_user = bool(pinned)
        has_match = bool(accept) or pinned_by_user
        # a generic sentence (an opening or a call to action without a match) needs no match of its own, unless it
        # names something written (a price, a code): then a frame showing it is what it needs (dev2 blower u6)
        generic = not has_match and unit.function in ("hook", "cta", "intro", "outro") and not about_text(unit) \
            and unit.unit_id not in on_card
        review = mode != "content" or (not generic and milli < GOOD_COVERAGE)
        coverage = {"need_ms": unit.length_ms, "illustrated_ms": illustrated, "milli": milli,
                    "status": "generic" if generic else ("full" if milli >= GOOD_COVERAGE else ("partial" if milli else "none")),
                    "needs_review": review, "matched_footage": len(accept), "best_relevance": best, "user_ms": user_ms}
        decision.coverage = coverage
        note = "" if generic else _coverage_note(milli, bool(accept))
        if spoken_ms:  # said in a note, the log's coverage keys stay those of the contract (editorial-v1)
            note = (f"{spoken_ms / 1000:.1f} giây cảnh chỉ khớp lời nói trong nguồn (phụ đề/transcript), chưa có bằng chứng "
                    "hình ảnh: cần kiểm tra" + (f"; {note}" if note else ""))
        if weak_spoken_ms:  # the speech no longer lifts a weak look over the bar: say why the sentence is not READY
            note = (f"{weak_spoken_ms / 1000:.1f} giây cảnh có lời nói trong nguồn khớp câu nhưng bằng chứng hình ảnh còn yếu "
                    f"(độ tin cậy dưới {REVIEW_BELOW}): cần kiểm tra" + (f"; {note}" if note else ""))
        if pinned_by_user and user_ms:
            note = "cảnh do người dùng chọn; AI không thấy cảnh này khớp lời câu" if not milli else \
                f"chỉ {milli // 10} % thời lượng câu có cảnh AI thấy khớp lời; phần còn lại là cảnh người dùng chọn"
        if not has_match and unit.unit_id in on_card:
            note = "lời câu chỉ khớp một thẻ chữ (đã để dành cho câu nói về chữ): dùng cảnh thật, cần kiểm tra"
        if note:
            decision.notes.append(note)
        # review: the evidence decides, not the render: a generic sentence on usable footage needs none, an
        # illustrated one needs it when its coverage or the match is weak
        # even a generic sentence is looked at when its footage is choppy or shown before: a repeat, a piece too short
        # to read, or more pieces than its length holds (dev2 blower u6: five pieces in 3.9 s, one a repeat)
        choppy = generic and (any("nhắc lại cảnh" in r for c in mine for r in c.reasons)
                              or len(mine) > max(2, unit.length_ms // max(1, pacing.minimum))
                              or any(c.length_ms < pacing.minimum // 2 for c in mine))
        if choppy:
            decision.notes.append("câu mở đầu/kêu gọi dùng cảnh lặp hoặc cắt vụn: cần kiểm tra")
            coverage["needs_review"] = True
        # an opening that speaks of something the footage half-shows (some of its words, too few for a match) is not
        # a generic line: the shot it needs may be there under other words (rc4 Hũ tăm: "ấn" vs the AI's "nhấn")
        half_matched = generic and mode == "content" and 0 < best < MIN_RELEVANCE and not pinned
        if half_matched:
            decision.notes.append("câu mở đầu/kêu gọi nói tới nội dung cụ thể nhưng cảnh chỉ khớp một phần lời: cần kiểm tra")
            coverage["needs_review"] = True
        # a product's opening with no match of its own needs no shot of its own, but it needs the product: over footage
        # the vision looked at and saw no product in, what it says of the product is shown on something else
        # (rc7 D3b, P1-N1: "Chiếc nồi chiên không dầu này có lòng nồi chống dính…" READY on 3D dominoes). Unknown
        # (no vision flag: a note, a transcript) is not "absent".
        unseen = generic and product and unit.function == "hook" and mode == "content" \
            and not pinned and _product_unseen(mine, evidence)
        if unseen:
            decision.notes.append("câu mở đầu nói về sản phẩm nhưng AI không thấy sản phẩm trong cảnh được chọn: cần kiểm tra")
            coverage["needs_review"] = True
        # an opening or closing that still shows a later sentence's own shot (nothing else was left) is never silent
        spoiler = unit.function in OPENING_CLOSING and not pinned and any(
            owners_after(unit, c.candidate.candidate_id) for c in mine)
        if spoiler:
            decision.notes.append("câu mở đầu/kết dùng cảnh của một câu phía sau (nguồn không còn cảnh khác): cần kiểm tra")
            coverage["needs_review"] = True

        # what the sentence says is displayed must be in the chosen shots: a sentence about a map, a card or a line of
        # text whose shots show none of its number, title or stated words, or a shot showing another amount of the same
        # kind, is never READY on shared words alone (#802). A pin is the user's choice and says so already.
        claim_result = None
        unproven = False
        if stated[unit.unit_id].any and mode == "content" and not pinned_by_user:
            per_piece = [checked.get((unit.unit_id, c.candidate.candidate_id)) or said.Check() for c in mine]
            claim_result = said.merged(per_piece)
            contradicted = [v for piece in per_piece for v in piece.conflicts]
            # only a sentence about something displayed: a number spoken over a scene names no figure the shot must show
            # a disputed count (#817) is CONTRADICTED for the review queue even when another piece shows it: never READY
            unproven = stated[unit.unit_id].display and bool(contradicted or claim_result.unverified or claim_result.conflicts
                                                             or claim_result.disputed)
            if contradicted and not claim_result.conflicts:
                claim_result.conflicts = list(dict.fromkeys(contradicted))
            if unproven:
                decision.notes.append(said.note(claim_result))
                coverage["needs_review"] = True

        def flag(c: Choice) -> bool:
            if not c.candidate.quality.usable or mode != "content" or spoiler or unproven:
                return True
            if generic:
                # its pieces carry the flag too: the plan and the checks read the pieces (rc5: still silent on rc4 data)
                return choppy or half_matched or unseen
            return review or c.confidence < REVIEW_BELOW
        mine[:] = [Choice(c.unit_id, c.candidate, c.offset_ms, c.length_ms, c.score, c.confidence, flag(c), c.reasons) for c in mine]
        decision.signals = _signals(unit, mine, accept, best, milli, coverage["needs_review"], mode, evidence,
                                    lambda c: scorer.content_relevance(unit, c, relevance_of[(unit.unit_id, c.candidate_id)][0]),
                                    still_ids)
        if claim_result is not None:
            decision.signals["claims"] = {"display": stated[unit.unit_id].display, **claim_result.to_dict()}
    choices = [c for u in found_units for c in by_unit.get(u.unit_id, [])]
    return choices, [decisions[u.unit_id] for u in found_units], mode


def _trusted(info: ev.Evidence | None) -> int:
    """How far what can verify a shot's picture is trusted (milli): the vision, text read off the frame, the user's
    word; never its file name, never the speech over it (#836)."""
    return max([i.confidence * ev.TRUST[i.origin] // 1000 for i in info.items if ev.verifies(i)] or [0]) if info else 0


def _product_unseen(mine: Sequence[Choice], evidence: Mapping[str, ev.Evidence]) -> bool:
    """Whether the vision looked at the shots of ``mine`` and saw the product in none of them."""
    seen = [getattr(evidence.get(c.candidate.candidate_id), "product_visible", None) for c in mine]
    return bool(seen) and True not in seen and False in seen


def _signals(unit: Unit, mine: Sequence[Choice], accept: set[str], best: int, milli: int, review: bool, mode: str,
             evidence: Mapping[str, ev.Evidence], relevance: Any, stills: set[str]) -> dict[str, Any]:
    """The separate signals behind a sentence's verdict (Gate S final closure B), each on its own scale.

    evidence_reliability: how far what is known of the shots can be trusted (content origins only: a file name is
    never content), the weakest of its pieces; semantic_relevance: how well the content says what the sentence says
    (its best match in the footage, the weakest chosen match); temporal_coverage: share of its time on footage that
    illustrates it; allocation: where its time went (own footage, neutral, repeats, held frames); review_risk: the
    lowest piece confidence and whether the sentence needs a look. Nothing here changes a choice or a verdict.
    """
    trust, chosen_relevance = [], []
    own = neutral = repeat = held = 0
    for c in mine:
        info = evidence.get(c.candidate.candidate_id)
        trust.append(_trusted(info))
        if any("nhắc lại cảnh" in r for r in c.reasons):
            repeat += c.length_ms
        elif c.candidate.candidate_id in stills:
            held += c.length_ms
        elif c.candidate.candidate_id in accept:
            own += c.length_ms
            chosen_relevance.append(relevance(c.candidate))
        else:
            neutral += c.length_ms
    return {
        "evidence_reliability": {"mode": mode, "min_trust": min(trust, default=0), "max_trust": max(trust, default=0)},
        "semantic_relevance": {"best": best, "chosen_min": min(chosen_relevance, default=0),
                               "matched_footage": len(accept)},
        "temporal_coverage": {"milli": milli, "need_ms": unit.length_ms},
        "allocation": {"own_ms": own, "neutral_ms": neutral, "repeat_ms": repeat, "held_ms": held, "pieces": len(mine)},
        "review_risk": {"min_confidence": min((c.confidence for c in mine), default=0),
                        "flagged_pieces": sum(c.needs_review for c in mine), "needs_review": bool(review)},
    }


def _room(pick: dict[str, Any], picks: Sequence[dict[str, Any]], still_ids: set[str]) -> int:
    """How much longer ``pick`` can play on in its own source without running past the shot or into another
    piece of the same shot."""
    candidate = pick["candidate"]
    if candidate.candidate_id in still_ids:
        return 10**9
    end = pick["offset"] + pick["length"]
    limit = candidate.end_ms
    for other in picks:
        if other is not pick and other["candidate"].candidate_id == candidate.candidate_id and other["offset"] >= end:
            limit = min(limit, other["offset"])
    return max(0, limit - end)


def _settle(picks: list[dict[str, Any]], need: int, usable: Sequence[Candidate], still_ids: set[str], accept: set[str], *,
            used: set[str] = frozenset(), forward_only: bool = False, source_end: Mapping[str, int] | None = None,
            claimed: set[str] = frozenset()) -> None:
    """The invariants of a sentence's pieces, whatever the steps before left: every piece inside its shot, none
    shorter than ``SHORT_FLOOR`` when there is more than one, and together exactly ``need``. Time a piece cannot
    hold goes to the pieces that can play on; when none can, a shot is shown again as a declared repeat."""
    for pick in picks:  # inside the shot
        if pick["candidate"].candidate_id not in still_ids:
            pick["length"] = min(pick["length"], max(0, pick["candidate"].end_ms - pick["offset"]))
    picks[:] = [p for p in picks if p["length"] > 0]
    while len(picks) > 1:  # no flash: the shortest sliver gives its time to the others
        sliver = min(picks, key=lambda p: p["length"])
        if sliver["length"] >= SHORT_FLOOR:
            break
        picks.remove(sliver)

    def fill() -> int:
        short = need - sum(p["length"] for p in picks)
        for pick in sorted(picks, key=lambda p: -_room(p, picks, still_ids)):  # play on where the shot goes on
            if short <= 0:
                break
            extra = min(short, _room(pick, picks, still_ids))
            pick["length"] += extra
            short -= extra
        return short

    short = fill()
    guard = 0
    source_end = source_end or {}
    while short > 0 and guard < 64:
        guard += 1
        shown = {p["candidate"].candidate_id for p in picks}
        # footage nobody has shown yet first (in a forward-only source, only ahead of what has played) ...
        fresh = [c for c in usable if c.candidate_id not in shown and c.candidate_id not in used
                 and c.candidate_id not in still_ids and c.length_ms >= min(short, SHORT_FLOOR)
                 and (not forward_only or c.start_ms >= source_end.get(c.source_id, 0))]
        if fresh:
            new = sorted(fresh, key=lambda c: (c.candidate_id not in accept, c.candidate_id in claimed,
                                             -min(c.length_ms, short), c.source_id, c.start_ms))[0]
            length = min(new.length_ms, max(short, SHORT_FLOOR))
            piece = {"candidate": new, "offset": new.start_ms, "length": length, "tier": "neutral", "repeat": False,
                     "score": 0, "confidence": SIGNALS_ONLY_CAP if new.candidate_id not in accept else REVIEW_BELOW,
                     "reasons": ["thêm cảnh để đủ thời lượng câu"]}
            # forward only: where the source order puts it among this sentence's pieces
            at = len(picks)
            if forward_only:
                at = next((i for i, p in enumerate(picks) if p["candidate"].source_id == new.source_id
                           and p["offset"] > new.start_ms), len(picks))
            picks.insert(at, piece)
            short -= length
            if short < 0:
                over = -short
                for pick in sorted((p for p in picks if p is not piece), key=lambda p: -p["length"]):
                    cut = min(over, max(0, pick["length"] - SHORT_FLOOR))
                    pick["length"] -= cut
                    over -= cut
                piece["length"] -= over
                short = 0
            short = max(short, fill()) if short > 0 else 0
            continue
        # ... and only when there is none, a shot shown again, said so
        ranked = sorted((c for c in usable if c.candidate_id in still_ids or c.length_ms >= min(short, SHORT_FLOOR)),
                        key=lambda c: (c.candidate_id not in accept, c.candidate_id not in shown, -c.length_ms, c.candidate_id))
        if not ranked:
            ranked = sorted(usable, key=lambda c: -c.length_ms)
        again = ranked[0]
        length = short if again.candidate_id in still_ids else min(again.length_ms, max(short, SHORT_FLOOR))
        picks.append({"candidate": again, "offset": again.start_ms, "length": length, "tier": "repeat", "repeat": True,
                      "score": 0, "confidence": SIGNALS_ONLY_CAP,
                      "reasons": ["chiếu lại cảnh để đủ thời lượng câu (nguồn không đủ cảnh)"]})
        short -= length
        if short < 0:  # the repeat took a full piece: the others give way, never under the floor
            over = -short
            for pick in sorted(picks[:-1], key=lambda p: -p["length"]):
                cut = min(over, max(0, pick["length"] - SHORT_FLOOR))
                pick["length"] -= cut
                over -= cut
            picks[-1]["length"] -= over  # what no piece could give: the repeat itself is shorter
            short = 0
        short = max(short, fill()) if short > 0 else 0
    while len(picks) > 1:  # a sliver left by the repeat: merged when the other pieces can take its time
        sliver = min(picks, key=lambda p: p["length"])
        if sliver["length"] >= SHORT_FLOOR:
            break
        before = [(p, p["length"]) for p in picks]
        picks.remove(sliver)
        if fill() > 0:
            picks[:] = [p for p, _ in before]
            for p, length in before:
                p["length"] = length
            break


__all__ = ["Choice", "Decision", "Edits", "FUNCTIONS", "Scorer", "Unit", "assign", "product_function", "units"]

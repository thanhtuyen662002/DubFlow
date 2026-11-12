"""Optional vision evidence: what the user's multimodal AI model sees on each keyframe (ADR-0054 §3).

Only used when the job has an AI route whose provider preset accepts images
(``ai.client.Preset.vision``) and the user's key. Keyframes go in small batches
with one fixed instruction and a JSON schema; each answer is validated (labels
from the closed vocabulary, lengths, ranges, a sane product box) and cached by
frame content × provider × model × prompt version, so planning again, editing
the script or replacing another clip asks nothing new. A frame budget bounds the
cost of one job.

Whatever goes wrong (no key, a text-only model, a refusal, a time-out, quota,
a malformed answer), the candidate simply has no vision evidence and the job
gets a warning: vision never blocks a video, needs no GPU and is never required.
The model is told to say ``unknown`` instead of guessing, and its descriptions
are evidence for choosing shots only; they never become claims in a script.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import evidence as ev
from .candidates import Candidate

PROMPT_VERSION = "vision-v3"  # v2: Vietnamese with its marks, main product; v3: confidence and hook on 0–1000
BATCH = 4  # six frames with full Vietnamese answers ran past the token limit
DEFAULT_BUDGET = 60
# A long programme's budget follows its length when the user set none (P2-N11): this many frames per narrated
# sentence, never under the default and never over the cap (the most a spec may ask for). Reported in the run.
FRAMES_PER_SENTENCE = 2
LONG_FORM_CAP = 400


def auto_budget(base: int, sentences: int) -> int:
    return max(base, min(LONG_FORM_CAP, FRAMES_PER_SENTENCE * max(0, sentences)))
MAX_TEXT = 300

SYSTEM = (
    "Bạn là trợ lý dựng phim. Với mỗi khung hình được đánh số, hãy mô tả đúng những gì NHÌN THẤY, bằng tiếng Việt có dấu đầy đủ (ví dụ “đồng hồ áp suất”, không viết “dong ho ap suat”), ngắn gọn. "
    "Không suy đoán thương hiệu, tính năng hay thông số không thấy rõ trên hình. Nếu không chắc, dùng nhãn 'unknown' và độ tin cậy thấp. "
    "labels chỉ được chọn trong danh sách cho sẵn. product_box là khung chứa sản phẩm chính theo phần nghìn của khung hình "
    "(x, y, w, h từ 0 đến 1000), hoặc null nếu không có sản phẩm. product_group là một tên ngắn cho SẢN PHẨM CHÍNH/biến thể đang thấy (không phải phụ kiện hay đồ vật đi kèm như ly, tách, hộp), "
    "(ví dụ 'noi-chien-den', 'noi-chien-trang') để nhận ra các khung quay sản phẩm khác nhau; null nếu không có sản phẩm. "
    "hook là 0–1000: khung này hấp dẫn đến đâu để mở đầu video (kết quả đẹp, khoảnh khắc bất ngờ, hành động rõ). "
    "confidence là số nguyên từ 0 đến 1000 (1000 = chắc chắn mô tả đúng, KHÔNG dùng thang 0–100). "
    "clarity: clear, blurry, dark, obstructed."
)


def schema(labels: Sequence[str]) -> dict[str, Any]:
    frame = {
        "type": "object",
        "properties": {
            "frame": {"type": "integer"},
            "description": {"type": "string"},
            "objects": {"type": "array", "items": {"type": "string"}},
            "actions": {"type": "array", "items": {"type": "string"}},
            "visible_text": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string", "enum": [*labels, "unknown"]}},
            "confidence": {"type": "integer"},
            "clarity": {"type": "string", "enum": ["clear", "blurry", "dark", "obstructed"]},
            "product_box": {"type": ["object", "null"], "properties": {k: {"type": "integer"} for k in ("x", "y", "w", "h")}},
            "product_group": {"type": ["string", "null"]},
            "hook": {"type": "integer"},
        },
        "required": ["frame", "description", "labels", "confidence", "clarity"],
    }
    return {"type": "object", "properties": {"frames": {"type": "array", "items": frame}}, "required": ["frames"]}


@dataclass(frozen=True)
class Seen:
    """One validated answer about one frame."""

    description: str
    objects: tuple[str, ...]
    actions: tuple[str, ...]
    visible_text: str
    labels: tuple[str, ...]
    confidence: int
    clarity: str
    product_box: Any
    product_group: str | None
    hook: int

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "objects": list(self.objects), "actions": list(self.actions),
                "visible_text": self.visible_text, "labels": list(self.labels), "confidence": self.confidence,
                "clarity": self.clarity, "product_box": self.product_box, "product_group": self.product_group, "hook": self.hook}


def _clip_text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _int(value: Any, low: int, high: int, default: int) -> int:
    return max(low, min(high, value)) if type(value) is int else default


def validate(answer: Any, labels: Sequence[str]) -> Seen | None:
    """A model's answer about one frame, made safe; None when it is not an answer at all."""
    if not isinstance(answer, Mapping):
        return None
    description = _clip_text(answer.get("description"))
    found = tuple(dict.fromkeys(label for label in (answer.get("labels") or []) if isinstance(label, str) and label in labels))
    if not description and not found:
        return None
    clarity = answer.get("clarity") if answer.get("clarity") in ("clear", "blurry", "dark", "obstructed") else "clear"
    box = answer.get("product_box")
    group = answer.get("product_group")
    return Seen(description,
                tuple(_clip_text(o, 60) for o in (answer.get("objects") or [])[:12] if _clip_text(o, 60)),
                tuple(_clip_text(a, 80) for a in (answer.get("actions") or [])[:8] if _clip_text(a, 80)),
                _clip_text(answer.get("visible_text"), 200), found, _int(answer.get("confidence"), 0, 1000, 500), clarity,
                box if ev.box_from(box) is not None else None,
                _clip_text(group, 40) or None if isinstance(group, str) else None, _int(answer.get("hook"), 0, 1000, 0))


def _on_thousand(items: list[Any]) -> list[Any]:
    """Answers whose confidence and hook were given on another scale, put on 0–1000.

    Models asked for 0–1000 still answer 0–100 (90, 95, 42) or 0–1 (0.9); read alone, 90 would be a
    very low 90/1000. The scale is told from the whole batch: all values within 0–1, or within 0–100.
    """
    for key in ("confidence", "hook"):
        values = [item.get(key) for item in items if isinstance(item, Mapping) and type(item.get(key)) in (int, float)]
        if not values or max(values) > 100:
            factor = 1
        elif max(values) <= 1:
            factor = 1000
        else:
            factor = 10
        for item in items:
            if isinstance(item, dict) and type(item.get(key)) in (int, float):
                item[key] = round(item[key] * factor)
    return items


Ask = Callable[[str, Mapping[str, Any], list[dict[str, Any]]], tuple[Any, str]]


class Describer:
    """Asks a vision model about keyframes, with a cache and a frame budget."""

    def __init__(self, ask: Ask, *, provider: str, model: str, cache: Path, labels: Sequence[str] = ev.LABELS,
                 budget: int = DEFAULT_BUDGET, context: str = "") -> None:
        self.ask, self.provider, self.model, self.cache = ask, provider, model, cache
        self.labels, self.budget, self.context = tuple(labels), budget, context
        self.asked = 0
        self.taken = 0  # frames counted against the budget, cached or asked: a warm run sees the same frames as a cold one
        self.warnings: list[str] = []
        self.used_model = model

    def _key(self, data: bytes) -> str:
        return hashlib.sha256(b"|".join([PROMPT_VERSION.encode(), self.provider.encode(), self.model.encode(),
                                         ",".join(self.labels).encode(), hashlib.sha256(data).digest()])).hexdigest()

    def _cached(self, key: str) -> Seen | None:
        try:
            value = json.loads((self.cache / f"{key[:32]}.json").read_text(encoding="utf-8"))
            return validate(value, self.labels) if value.get("key") == key else None
        except (OSError, ValueError, AttributeError):
            return None

    def _store(self, key: str, seen: Seen) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / f"{key[:32]}.json"
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps({"key": key, **seen.to_dict()}, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, path)

    def describe(self, frames: Sequence[tuple[str, Path]], cancelled: Callable[[], bool] = lambda: False) -> dict[str, Seen]:
        """candidate id → what the model saw, for the frames it could see (cached ones first, then the budget)."""
        seen: dict[str, Seen] = {}
        todo: list[tuple[str, bytes, str]] = []
        readable = []
        for candidate_id, path in frames:
            try:
                readable.append((candidate_id, path.read_bytes()))
            except OSError:
                continue
        room = max(0, self.budget - self.taken)
        if len(readable) > room:
            self.warnings.append(f"Giới hạn {self.budget} khung hình cho AI nhìn mỗi lần: {len(readable) - room} cảnh không được AI xem.")
            readable = readable[:room]
        self.taken += len(readable)
        for candidate_id, data in readable:
            key = self._key(data)
            cached = self._cached(key)
            if cached is not None:
                seen[candidate_id] = cached
            else:
                todo.append((candidate_id, data, key))
        batches = [todo[start:start + BATCH] for start in range(0, len(todo), BATCH)]
        while batches:
            if cancelled():
                break
            batch = batches.pop(0)
            parts: list[dict[str, Any]] = [{"text": (f"Bối cảnh: {self.context}\n" if self.context else "")
                                            + f"Có {len(batch)} khung hình, đánh số từ 1. Nhãn được dùng: {', '.join(self.labels)}."}]
            for number, (_, data, _) in enumerate(batch, start=1):
                parts.append({"text": f"Khung {number}:"})
                parts.append({"image": {"mediaType": "image/jpeg", "data": base64.b64encode(data).decode("ascii")}})
            self.asked += len(batch)
            try:
                answer, used = self.ask(SYSTEM, schema(self.labels), parts)
                self.used_model = used or self.model
            except Exception as error:  # noqa: BLE001 - vision is optional; the plan says it went without
                message = getattr(error, "message", "") or type(error).__name__
                if getattr(error, "code", "") == "FORMAT" and len(batch) > 1:
                    # a cut-off answer: ask again in halves (the frames are not counted twice)
                    self.asked -= len(batch)
                    half = len(batch) // 2
                    batches[:0] = [batch[:half], batch[half:]]
                    continue
                self.warnings.append(f"AI không xem được {len(batch)} khung hình ({message[:120]}); các cảnh này chọn theo thông tin khác.")
                if getattr(error, "code", "") in ("KEY", "BALANCE", "QUOTA", "LIMIT", "MODEL", "REFUSED"):
                    break  # the same answer for every batch: stop asking
                continue
            frames_out = answer.get("frames") if isinstance(answer, Mapping) else None
            frames_out = _on_thousand(frames_out if isinstance(frames_out, list) else [])
            for item in frames_out:
                number = item.get("frame") if isinstance(item, Mapping) else None
                if type(number) is not int or not 1 <= number <= len(batch):
                    continue
                checked = validate(item, self.labels)
                if checked is not None:
                    candidate_id, _, key = batch[number - 1]
                    seen[candidate_id] = checked
                    self._store(key, checked)
        return seen


def item_of(seen: Seen, detail: str) -> ev.Item:
    """A validated answer as one evidence item (an unclear frame counts half)."""
    confidence = seen.confidence if seen.clarity == "clear" else seen.confidence // 2
    text = " ".join(part for part in (seen.description, ", ".join(seen.objects), ", ".join(seen.actions), seen.visible_text) if part)
    return ev.Item("vision", confidence, text, seen.labels, detail)


def apply(found: Mapping[str, Seen], evidence: Mapping[str, ev.Evidence], detail: str) -> None:
    """Turn validated answers into evidence items (and boxes, hook scores, product groups)."""
    for candidate_id, seen in found.items():
        item = evidence.get(candidate_id)
        if item is None:
            continue
        item.add(item_of(seen, detail))
        item.product_box = ev.box_from(seen.product_box)
        item.hook = seen.hook
        item.product_visible = seen.product_box is not None or any(label.startswith("product_") for label in seen.labels)
        item.same_product = seen.product_group


def route_ask(route: Any, *, transport: Any = None) -> Ask:
    """An ``Ask`` over the shared AI client (the user's provider, model and key)."""
    from engine.dubflow.ai import client

    def ask(system: str, schema_value: Mapping[str, Any], parts: list[dict[str, Any]]) -> tuple[Any, str]:
        kwargs = {"max_tokens": 8192, "timeout": 120.0}
        if transport is not None:
            kwargs["transport"] = transport
        value, used = client.ask(route, system, schema_value, parts, **kwargs)
        return value, used.model
    return ask


def factory(ai: Mapping[str, str] | None, key: str, budget: int) -> Callable[..., Describer] | None:
    """A maker of describers on the user's AI route, or None (no AI chosen, no key, no budget or a text-only model)."""
    if not ai or not key or budget <= 0:
        return None
    from engine.dubflow.ai.client import AiError, make_route
    try:
        route = make_route(ai["provider"], key, model=ai.get("model"), base_url=ai.get("base_url"))
    except AiError:
        return None
    if not route.preset.vision:
        return None

    def make(cache: Path, *, context: str = "", labels: Sequence[str] = ev.LABELS) -> Describer:
        return Describer(route_ask(route), provider=route.provider, model=route.model, cache=cache, labels=labels,
                         budget=budget, context=context)
    return make


def text_asker(ai: Mapping[str, str] | None, key: str) -> Callable[[str, Mapping[str, Any], list[dict[str, Any]]], Any] | None:
    """A JSON question to the user's AI (any chat model), or None without an AI or a key."""
    if not ai or not key:
        return None
    from engine.dubflow.ai import client
    try:
        route = client.make_route(ai["provider"], key, model=ai.get("model"), base_url=ai.get("base_url"))
    except client.AiError:
        return None

    def ask(system: str, schema_value: Mapping[str, Any], parts: list[dict[str, Any]]) -> Any:
        return client.ask(route, system, schema_value, parts, max_tokens=4000, timeout=120.0)[0]
    return ask


def frames_for(candidates: Sequence[Candidate], folder: Path) -> list[tuple[str, Path]]:
    """The keyframes to show, usable candidates first (the budget then goes to footage that can be used)."""
    ordered = sorted(candidates, key=lambda c: (not c.quality.usable, c.source_id, c.start_ms))
    return [(c.candidate_id, folder / c.keyframe) for c in ordered if c.keyframe]


def broad(candidates: Sequence[Candidate], folder: Path, room: int) -> list[tuple[str, Path]]:
    """The first look (adaptive sampling, step 1): at most ``room`` keyframes spread evenly over every source and
    its length, usable footage first, instead of the first ``room`` shots in source order (a long set's budget used
    to end inside its first source)."""
    if room <= 0:
        return []
    usable = [c for c in sorted(candidates, key=lambda c: (c.source_id, c.start_ms)) if c.keyframe and c.quality.usable]
    rest = [c for c in sorted(candidates, key=lambda c: (c.source_id, c.start_ms)) if c.keyframe and not c.quality.usable]
    if len(usable) > room:
        step = len(usable) / room
        usable = [usable[int(i * step + step / 2)] for i in range(room)]
    picked = usable + rest[:room - len(usable)]
    return [(c.candidate_id, folder / c.keyframe) for c in picked]


def spread_order(items: Sequence[Any]) -> list[Any]:
    """``items`` reordered so that every prefix is spread evenly over them: the middle, then the quarters, the
    eighths… (a look cut short by the budget still covers the whole stretch, not its beginning)."""
    n = len(items)
    if n <= 2:
        return list(items)
    order: list[int] = []
    seen: set[int] = set()
    bits = max(1, (n - 1).bit_length())
    for k in range(1 << bits):
        fraction = int(format(k, f"0{bits}b")[::-1], 2) / (1 << bits)  # base-2 van der Corput
        index = min(n - 1, int(fraction * n + 0.5 / (1 << bits) * n))
        if index not in seen:
            seen.add(index)
            order.append(index)
    order += [i for i in range(n) if i not in seen]
    return [items[i] for i in order]


def allocate(parts: Sequence[Sequence[Candidate]], folder: Path, room: int,
             groups: Mapping[str, str] | None = None) -> list[tuple[str, Path]]:
    """The first look over a programme (P2-N11): only the footage its parts use (inside their trims), each part's
    usable shots in an evenly spread order, the parts taking turns so that every part is looked at before any part
    gets a second turn, and one frame per duplicate group (the same picture in two places is looked at once)."""
    if room <= 0:
        return []
    groups = groups or {}
    queues = [spread_order(sorted((c for c in pool if c.keyframe and c.quality.usable), key=lambda c: (c.source_id, c.start_ms)))
              for pool in parts]
    picked: list[Candidate] = []
    taken: set[str] = set()
    while len(picked) < room and any(queues):
        for queue in queues:
            while queue and groups.get(queue[0].candidate_id, queue[0].candidate_id) in taken:
                queue.pop(0)
            if queue and len(picked) < room:
                chosen = queue.pop(0)
                picked.append(chosen)
                taken.add(groups.get(chosen.candidate_id, chosen.candidate_id))
    return [(c.candidate_id, folder / c.keyframe) for c in picked]


def share(found: Mapping[str, Seen], groups: Mapping[str, str], pool: Sequence[Candidate]) -> dict[str, Seen]:
    """``found`` extended to the shots of the same duplicate group (one picture, looked at once)."""
    out = dict(found)
    by_group: dict[str, Seen] = {}
    for candidate_id, seen in found.items():
        by_group.setdefault(groups.get(candidate_id, candidate_id), seen)
    for c in pool:
        group = groups.get(c.candidate_id, c.candidate_id)
        if c.candidate_id not in out and group in by_group:
            out[c.candidate_id] = by_group[group]
    return out


def coverage_map(pool: Sequence[Candidate], evidence: Mapping[str, ev.Evidence], *, gaps: int = 5) -> dict[str, Any]:
    """What of a part's footage has content evidence (P2-N11): shots and ms with evidence, and the largest stretches
    no evidence covers (where a sentence's footage may be without anyone having looked)."""
    content = {c.candidate_id for c in pool if c.candidate_id in evidence and evidence[c.candidate_id].content}
    total = sum(c.length_ms for c in pool)
    seen_ms = sum(c.length_ms for c in pool if c.candidate_id in content)
    stretches: list[list[Any]] = []
    for source in sorted({c.source_id for c in pool}):
        mine = sorted((c for c in pool if c.source_id == source), key=lambda c: c.start_ms)
        start = None
        for c in mine + [None]:
            if c is not None and c.candidate_id not in content:
                start = c.start_ms if start is None else start
                end = c.end_ms
                continue
            if start is not None:
                stretches.append([source, start, end])
                start = None
    stretches.sort(key=lambda s: (-(s[2] - s[1]), s[0], s[1]))
    return {"shots": len(pool), "shots_with_evidence": len(content), "ms": total, "ms_with_evidence": seen_ms,
            "milli": seen_ms * 1000 // max(1, total), "unseen": stretches[:gaps]}


def targets(units: Sequence[Mapping[str, Any]], pool: Sequence[Candidate], evidence: Mapping[str, ev.Evidence],
            looked: set[str], limit: int, *, forward_only: bool) -> list[str]:
    """Shots worth a targeted look (adaptive sampling, steps 2–3) for the sentences the first plan could not cover.

    ``units`` is the editorial log's ``units`` of a plan. For each sentence flagged partial or uncovered, the
    candidates not yet seen are searched by what other evidence (subtitles, notes, file names) says about them; the
    rest are spread over the stretch of footage the sentence may use (forward only: between what the sentences
    around it chose). Sentences take turns until ``limit``.
    """
    from .align import Scorer, Unit  # align does not import vision

    weak = [u for u in units if (u.get("coverage") or {}).get("needs_review") and (u.get("coverage") or {}).get("status") in ("partial", "none")]
    if not weak or limit <= 0:
        return []
    ordered = sorted((c for c in pool if c.keyframe and c.quality.usable), key=lambda c: (c.source_id, c.start_ms))
    by_id = {c.candidate_id: c for c in ordered}
    scorer = Scorer(ordered, evidence)
    position = {u["unit_id"]: index for index, u in enumerate(units)}
    queues = []
    for unit in weak:
        here = position[unit["unit_id"]]
        low: dict[str, int] = {}
        high: dict[str, int] = {}
        if forward_only:
            for other in units:
                for chosen in other.get("chosen", ()):
                    c = by_id.get(chosen)
                    if c is None:
                        continue
                    if position[other["unit_id"]] < here:
                        low[c.source_id] = max(low.get(c.source_id, 0), c.end_ms)
                    elif position[other["unit_id"]] > here:
                        high[c.source_id] = min(high.get(c.source_id, c.start_ms), c.start_ms)
        window = [c for c in ordered if c.candidate_id not in looked and c.start_ms >= low.get(c.source_id, 0)
                  and c.end_ms <= high.get(c.source_id, c.end_ms)]
        wanted = Unit(unit["unit_id"], unit.get("text", ""), 0, 1, unit.get("function", "narration"))
        found = sorted(((scorer.relevance(wanted, c)[0], c) for c in window), key=lambda pair: -pair[0])
        matched = [c for value, c in found if value > 0]
        others = [c for value, c in found if value <= 0]
        others.sort(key=lambda c: (c.source_id, c.start_ms))
        if others:  # evenly over the stretch, not its first shots
            step = max(1.0, len(others) / max(1, limit))
            others = [others[int(i * step)] for i in range(min(len(others), limit))]
        queues.append(matched + others)
    picked: list[str] = []
    while len(picked) < limit and any(queues):
        for queue in queues:
            while queue and queue[0].candidate_id in picked:
                queue.pop(0)
            if queue and len(picked) < limit:
                picked.append(queue.pop(0).candidate_id)
    return picked


__all__ = ["Describer", "PROMPT_VERSION", "Seen", "allocate", "apply", "broad", "coverage_map", "share", "spread_order", "item_of", "factory", "frames_for", "route_ask", "schema", "targets",
           "text_asker", "validate"]

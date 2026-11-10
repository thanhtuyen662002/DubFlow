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

PROMPT_VERSION = "vision-v2"  # v2: Vietnamese with its marks, product group = the main product
BATCH = 6
DEFAULT_BUDGET = 60
MAX_TEXT = 300

SYSTEM = (
    "Bạn là trợ lý dựng phim. Với mỗi khung hình được đánh số, hãy mô tả đúng những gì NHÌN THẤY, bằng tiếng Việt có dấu đầy đủ (ví dụ “đồng hồ áp suất”, không viết “dong ho ap suat”), ngắn gọn. "
    "Không suy đoán thương hiệu, tính năng hay thông số không thấy rõ trên hình. Nếu không chắc, dùng nhãn 'unknown' và độ tin cậy thấp. "
    "labels chỉ được chọn trong danh sách cho sẵn. product_box là khung chứa sản phẩm chính theo phần nghìn của khung hình "
    "(x, y, w, h từ 0 đến 1000), hoặc null nếu không có sản phẩm. product_group là một tên ngắn cho SẢN PHẨM CHÍNH/biến thể đang thấy (không phải phụ kiện hay đồ vật đi kèm như ly, tách, hộp), "
    "(ví dụ 'noi-chien-den', 'noi-chien-trang') để nhận ra các khung quay sản phẩm khác nhau; null nếu không có sản phẩm. "
    "hook là 0–1000: khung này hấp dẫn đến đâu để mở đầu video (kết quả đẹp, khoảnh khắc bất ngờ, hành động rõ). "
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


Ask = Callable[[str, Mapping[str, Any], list[dict[str, Any]]], tuple[Any, str]]


class Describer:
    """Asks a vision model about keyframes, with a cache and a frame budget."""

    def __init__(self, ask: Ask, *, provider: str, model: str, cache: Path, labels: Sequence[str] = ev.LABELS,
                 budget: int = DEFAULT_BUDGET, context: str = "") -> None:
        self.ask, self.provider, self.model, self.cache = ask, provider, model, cache
        self.labels, self.budget, self.context = tuple(labels), budget, context
        self.asked = 0
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
        for candidate_id, path in frames:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            key = self._key(data)
            cached = self._cached(key)
            if cached is not None:
                seen[candidate_id] = cached
            else:
                todo.append((candidate_id, data, key))
        if len(todo) > max(0, self.budget - self.asked):
            skipped = len(todo) - max(0, self.budget - self.asked)
            self.warnings.append(f"Giới hạn {self.budget} khung hình cho AI nhìn mỗi lần: {skipped} cảnh không được AI xem.")
            todo = todo[:max(0, self.budget - self.asked)]
        for start in range(0, len(todo), BATCH):
            if cancelled():
                break
            batch = todo[start:start + BATCH]
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
                self.warnings.append(f"AI không xem được {len(batch)} khung hình ({message[:120]}); các cảnh này chọn theo thông tin khác.")
                if getattr(error, "code", "") in ("KEY", "BALANCE", "QUOTA", "LIMIT", "MODEL", "REFUSED"):
                    break  # the same answer for every batch: stop asking
                continue
            frames_out = answer.get("frames") if isinstance(answer, Mapping) else None
            for item in frames_out if isinstance(frames_out, list) else []:
                number = item.get("frame") if isinstance(item, Mapping) else None
                if type(number) is not int or not 1 <= number <= len(batch):
                    continue
                checked = validate(item, self.labels)
                if checked is not None:
                    candidate_id, _, key = batch[number - 1]
                    seen[candidate_id] = checked
                    self._store(key, checked)
        return seen


def apply(found: Mapping[str, Seen], evidence: Mapping[str, ev.Evidence], detail: str) -> None:
    """Turn validated answers into evidence items (and boxes, hook scores, product groups)."""
    for candidate_id, seen in found.items():
        item = evidence.get(candidate_id)
        if item is None:
            continue
        confidence = seen.confidence if seen.clarity == "clear" else seen.confidence // 2
        text = " ".join(part for part in (seen.description, ", ".join(seen.objects), ", ".join(seen.actions), seen.visible_text) if part)
        item.add(ev.Item("vision", confidence, text, seen.labels, detail))
        item.product_box = ev.box_from(seen.product_box)
        item.hook = seen.hook
        item.product_visible = seen.product_box is not None or any(label.startswith("product_") for label in seen.labels)
        item.same_product = seen.product_group


def route_ask(route: Any, *, transport: Any = None) -> Ask:
    """An ``Ask`` over the shared AI client (the user's provider, model and key)."""
    from engine.dubflow.ai import client

    def ask(system: str, schema_value: Mapping[str, Any], parts: list[dict[str, Any]]) -> tuple[Any, str]:
        kwargs = {"max_tokens": 6000, "timeout": 120.0}
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


__all__ = ["Describer", "PROMPT_VERSION", "Seen", "apply", "factory", "frames_for", "route_ask", "schema", "text_asker", "validate"]

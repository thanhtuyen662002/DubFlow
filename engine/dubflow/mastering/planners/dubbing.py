"""Mastering for Lồng tiếng (``dubbing_preserve``, ADR-0055): measured, bounded, never a change of length or order.

From measurements (``mastering.measure``) and the options, decide:

- loudness: a linear two-pass ``loudnorm`` of the final mix to −16 LUFS / −1.5 dBTP, or nothing when the mix is
  already within 1 LU and under the ceiling, or silent;
- colour: a ``colorlevels`` stretch when the sampled frames' luma range is compressed, bounded per level (no
  saturation, no sharpen, no curve);
- framing (Balanced and Dynamic): only a window-box (dark bars on both axes) is cropped and scaled back uniformly,
  within the policy's ``max_zoom``; letterbox or pillarbox alone is left as it is;
- a climax suggestion (Dynamic, only when ``enable_climax_hook``): never applied, only recorded for review.

The decision is pure and records every reason, so the report can say what was done and what was left alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping

from .. import policy as rules
from ..measure import GREY_H, GREY_W, Loudness, Picture
from ..plan import Options

WORKFLOW = "dubbing_preserve"
RECIPE = "dubbing-master-v3"  # v2: captions from the final TTS; v3: linear loudness kept under the peak with AAC headroom
TARGET_LUFS, PEAK_DBTP, TARGET_LRA = -16.0, -1.5, 11.0
QUIET_LUFS = -45.0          # quieter than this the mix is (nearly) silent: nothing to level
CLOSE_LU = 1.0
AAC_HEADROOM_DB = 1.0    # peak the AAC encode can add over the mix's true peak
# How far colorlevels may move black and white (share of the 0–1 range) per level.
COLOR_LIMIT = {"light": 0.06, "balanced": 0.06, "dynamic": 0.10}
COLOR_MIN_GAIN = 0.02       # smaller stretches are not worth a filter
VIDEO_BLACK, VIDEO_WHITE = 16, 235
MIN_BAR = 3                 # pixels of GREY_W×GREY_H: thinner is noise or a soft edge


@dataclass(frozen=True)
class DubbingMaster:
    options: Options
    audio_filter: str | None = None
    video_filters: tuple[str, ...] = ()
    captions: str = "plain"                      # plain | phrase (Balanced/Dynamic: timed from the final TTS)
    climax: Mapping[str, Any] | None = None     # a suggestion, never rendered
    notes: tuple[str, ...] = ()
    measured: Mapping[str, Any] = field(default_factory=dict)

    @property
    def changes_render(self) -> bool:
        return bool(self.audio_filter or self.video_filters or self.captions != "plain")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "dubbing_mastering", "recipe": RECIPE, "options": self.options.to_dict(),
                "audio_filter": self.audio_filter, "video_filters": list(self.video_filters), "captions": self.captions,
                "climax": dict(self.climax) if self.climax else None, "notes": list(self.notes), "measured": dict(self.measured)}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _loudness(found: Loudness | None, sample_rate: int, notes: list[str]) -> str | None:
    """A linear gain only. ``loudnorm`` leaves linear mode (and compresses) when the gain would lift the measured
    peak over its ceiling, and AAC adds up to about 1 dB of peak (real run 9: a mix led to −16 LUFS peaked at
    −0.7 dBTP after encoding): when the peak leaves no room, the target is lowered so the gain stays linear with
    ``AAC_HEADROOM_DB`` to spare."""
    if found is None:
        notes.append("Âm lượng: bản trộn im lặng, không chỉnh.")
        return None
    if found.integrated < QUIET_LUFS:
        notes.append(f"Âm lượng: bản trộn quá nhỏ ({found.integrated:.1f} LUFS), không kéo lên để tránh khuếch đại nhiễu.")
        return None
    ceiling = PEAK_DBTP - AAC_HEADROOM_DB
    if abs(found.integrated - TARGET_LUFS) <= CLOSE_LU and found.true_peak <= ceiling:
        notes.append(f"Âm lượng: đã đạt {found.integrated:.1f} LUFS, đỉnh {found.true_peak:.1f} dBTP; giữ nguyên.")
        return None
    target = TARGET_LUFS
    if found.true_peak + (target - found.integrated) > ceiling:
        target = math.floor((found.integrated + ceiling - found.true_peak) * 10) / 10
        notes.append(f"Âm lượng: {found.integrated:.1f} → {target:.1f} LUFS (thấp hơn mục tiêu {TARGET_LUFS:.0f} LUFS vì đỉnh "
                     f"{found.true_peak:.1f} dBTP: giữ tăng/giảm tuyến tính, đỉnh ≤ {ceiling:.1f} dBTP trước khi mã hóa AAC)"
                     + (f"; nhỏ hơn bản trộn gốc {found.integrated - target:.1f} dB vì đỉnh đã cao." if target < found.integrated else "."))
    else:
        notes.append(f"Âm lượng: {found.integrated:.1f} → {TARGET_LUFS:.0f} LUFS, đỉnh ≤ {ceiling:.1f} dBTP trước khi mã hóa AAC "
                     f"(tuyến tính, 2 lượt đo).")
    lra = min(50.0, max(TARGET_LRA, math.ceil(found.lra)))  # never asks for less range than the mix has: no compression
    return (f"loudnorm=I={target:g}:TP={PEAK_DBTP:g}:LRA={lra:g}:measured_I={found.integrated:.2f}"
            f":measured_TP={found.true_peak:.2f}:measured_LRA={found.lra:.2f}:measured_thresh={found.threshold:.2f}"
            f":offset={found.offset:.2f}:linear=true:print_format=summary,aresample={sample_rate}")


def _colour(found: Picture | None, level: str, notes: list[str]) -> str | None:
    if found is None:
        notes.append("Màu: không đọc được khung hình mẫu, giữ nguyên.")
        return None
    span = VIDEO_WHITE - VIDEO_BLACK
    black = max(0.0, (found.low - VIDEO_BLACK) / span)
    white = min(1.0, (found.high - VIDEO_BLACK) / span)
    limit = COLOR_LIMIT[level]
    lift, drop = min(black, limit), min(1.0 - white, limit)
    if found.high - found.low < 40:
        notes.append("Màu: khung hình gần như một màu (cảnh tối/sáng có chủ ý), giữ nguyên.")
        return None
    if lift < COLOR_MIN_GAIN and drop < COLOR_MIN_GAIN:
        notes.append("Màu: dải sáng đã đủ rộng, giữ nguyên.")
        return None
    low, high = round(lift, 3), round(1.0 - drop, 3)
    notes.append(f"Màu: kéo giãn mức đen/trắng {low:.3f}–{high:.3f} (giới hạn {limit:.2f} mỗi đầu), không đổi độ bão hòa.")
    return f"colorlevels=rimin={low}:gimin={low}:bimin={low}:rimax={high}:gimax={high}:bimax={high}"


def _framing(found: Picture | None, policy: rules.Policy, width: int, height: int, notes: list[str]) -> str | None:
    if found is None or not policy.allows(rules.ZOOM):
        return None
    top, bottom, left, right = found.bars
    if top + bottom >= GREY_H - 10 or left + right >= GREY_W - 10:
        notes.append("Khung hình: khung mẫu quá tối để đo viền, giữ nguyên.")
        return None
    vertical = min(top, bottom) >= MIN_BAR
    horizontal = min(left, right) >= MIN_BAR
    if not (vertical and horizontal):
        if vertical or horizontal:
            notes.append("Khung hình: có viền đen một chiều; cắt sẽ làm méo hoặc mất hình, giữ nguyên.")
        else:
            notes.append("Khung hình: không có viền đen quanh hình, không cắt, không phóng.")
        return None
    # The same share of both axes keeps the shape: crop the smaller relative bar on both, then scale back.
    share = min(min(top, bottom) / GREY_H, min(left, right) / GREY_W)
    zoom = 1 / (1 - 2 * share)
    if zoom * 1000 > policy.max_zoom:
        share = (1 - 1000 / policy.max_zoom) / 2
        zoom = policy.max_zoom / 1000
    # Rounded up to even sizes: the zoom never exceeds the bound.
    crop_w, crop_h = math.ceil(width * (1 - 2 * share) / 2) * 2, math.ceil(height * (1 - 2 * share) / 2) * 2
    if crop_w >= width - 4 or crop_h >= height - 4:
        return None
    notes.append(f"Khung hình: bỏ viền đen bốn phía, phóng {zoom:.3f}× đều hai chiều (giới hạn {policy.max_zoom / 1000:.2f}×).")
    return f"crop={crop_w}:{crop_h}:{(width - crop_w) // 2}:{(height - crop_h) // 2},scale={width}:{height}:flags=lanczos,setsar=1"


def plan(options: Options, *, mix: Loudness | None, mix_rate: int | None, frames: Picture | None, width: int, height: int,
         climax: Mapping[str, Any] | None = None) -> DubbingMaster:
    """What mastering does to this dubbed video. ``mix`` is None when the source sound is kept (no dubbed mix)."""
    policy = rules.resolve(WORKFLOW, options)
    if not policy.enabled:
        return DubbingMaster(options, notes=("Mastering tắt: video xuất như cũ.",))
    notes: list[str] = []
    audio = None
    if policy.allows(rules.LOUDNESS):
        if mix_rate is None:
            notes.append("Âm lượng: không có bản lồng tiếng, giữ âm thanh gốc.")
        else:
            audio = _loudness(mix, mix_rate, notes)
    video: list[str] = []
    framing = _framing(frames, policy, width, height, notes)
    if framing:
        video.append(framing)
    if policy.allows(rules.COLOR_MATCH):
        colour = _colour(frames, options.level, notes)
        if colour:
            video.append(colour)
    captions = "plain"
    if policy.allows(rules.DYNAMIC_CAPTIONS):
        if mix_rate is None:
            notes.append("Phụ đề: không có giọng lồng tiếng để canh, giữ thời gian phụ đề như cũ.")
        else:
            captions = "phrase"
            notes.append(f"Phụ đề: canh theo lúc giọng Việt thật (TTS) được nghe, tối đa 2 dòng × 42 ký tự, "
                         f"tối thiểu 1 giây, không chồng nhau.")
    suggestion = None
    if policy.hook == "suggest" and climax:
        suggestion = {**climax, "needs_review": True, "applied": False}
        notes.append("Gợi ý đoạn cao trào làm teaser: chỉ đề xuất để duyệt, không chèn vào video.")
    elif policy.hook == "suggest":
        notes.append("Gợi ý teaser đã bật nhưng không đo được đoạn cao trào (thiếu âm thanh gốc); không đề xuất.")
    measured = {"mix": mix.to_dict() if mix else None, "picture": frames.to_dict() if frames else None}
    return DubbingMaster(options, audio, tuple(video), captions, suggestion, tuple(notes), measured)


__all__ = ["DubbingMaster", "RECIPE", "WORKFLOW", "plan"]

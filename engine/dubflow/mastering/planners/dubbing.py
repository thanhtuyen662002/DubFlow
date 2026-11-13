"""Mastering for Lồng tiếng (``dubbing_preserve``, ADR-0055): measured, bounded, never a change of length or order.

From measurements (``mastering.measure``) and the options, decide:

- loudness: a linear two-pass ``loudnorm`` of the final mix to −16 LUFS / −1.5 dBTP, or nothing when the mix is
  already within 1 LU and under the ceiling, or silent; the worker then encodes the result with the render's own AAC
  arguments, measures it, and when the encoded peak is over the ceiling lowers it by a bounded linear ``volume``
  (``aac_trim``; issue #821): the ceiling is checked after AAC, never only predicted;
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
# v2: captions from the final TTS; v3: linear loudness kept under the peak with AAC headroom; v4: the peak is measured
# after an AAC trial encode and trimmed linearly until it is under the ceiling (issue #821)
RECIPE = "dubbing-master-v4"
TARGET_LUFS, PEAK_DBTP, TARGET_LRA = -16.0, -1.5, 11.0
QUIET_LUFS = -45.0          # quieter than this the mix is (nearly) silent: nothing to level
CLOSE_LU = 1.0
AAC_HEADROOM_DB = 1.0    # peak the AAC encode can add over the mix's true peak
# After AAC (issue #821): the encode can add more than AAC_HEADROOM_DB (rc17: +1.20 dB), and how much is not monotonic
# in the gain (rc17: −0.2/−0.4/−0.6 dB more gave −2.29/−2.77/−2.54 dBTP), so every trim is encoded and measured again.
AAC_TRIM_MARGIN_DB = 0.3  # trimmed below the ceiling by this much, so a small re-encode variation stays under it
AAC_TRIM_TRIES = 3        # at most this many trimmed re-encodes after the first trial: bounded, never a loop
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
    # The AAC trial of the dubbed mix (issue #821): {"first_true_peak", "true_peak", "trim_db", "tries"}, or None when
    # there is no dubbed mix or the trial could not run (the export is still measured after the render).
    aac_trial: Mapping[str, Any] | None = None

    @property
    def changes_render(self) -> bool:
        return bool(self.audio_filter or self.video_filters or self.captions != "plain")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "kind": "dubbing_mastering", "recipe": RECIPE, "options": self.options.to_dict(),
                "audio_filter": self.audio_filter, "video_filters": list(self.video_filters), "captions": self.captions,
                "climax": dict(self.climax) if self.climax else None, "notes": list(self.notes), "measured": dict(self.measured),
                "aac_trial": dict(self.aac_trial) if self.aac_trial is not None else None}

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


def aac_trim(true_peak: float | None, trimmed_db: float = 0.0) -> float:
    """The next cumulative trim (dB, never negative: a trim only ever lowers) after an AAC trial measured
    ``true_peak`` with ``trimmed_db`` already taken off the mastered PCM. Always counted from the PCM (planner
    filter, then ``volume=-<trim>dB``), never from decoded AAC. Unchanged when the peak is under the ceiling or
    was not measured (silence)."""
    if trimmed_db < 0 or trimmed_db != trimmed_db:
        raise ValueError("trimmed_db must be a non-negative number")
    if true_peak is None or true_peak <= PEAK_DBTP:
        return trimmed_db
    return round(trimmed_db + (true_peak - PEAK_DBTP) + AAC_TRIM_MARGIN_DB, 2)


def with_trim(audio_filter: str | None, trim_db: float) -> str | None:
    """``audio_filter`` followed by a linear ``volume`` cut of ``trim_db`` (or the cut alone): no limiter, no
    compressor, no change of loudness range."""
    if trim_db < 0 or trim_db != trim_db:
        raise ValueError("trim_db must be a non-negative number")
    if trim_db == 0:
        return audio_filter
    gain = f"volume=-{trim_db:.2f}dB"
    return f"{audio_filter},{gain}" if audio_filter else gain


def aac_note(trial: Mapping[str, Any]) -> str | None:
    """What the AAC trial changed, for the plan's notes. Its prefix is deliberately not "Âm lượng:" (that is the
    planner's own loudness note, which the card and the B1 fallback look up by that exact prefix)."""
    trim, peak, tries = float(trial.get("trim_db") or 0.0), trial.get("true_peak"), int(trial.get("tries") or 0)
    if peak is not None and peak > PEAK_DBTP:
        if trim > 0:
            done = f"đã giảm thêm {_vi(trim)} dB qua {tries} lần thử mã hóa nhưng đỉnh vẫn"
        elif tries > 1:  # every trim was tried and discarded: none measured lower than the untrimmed mix
            done = f"đã thử giảm {tries - 1} lần nhưng không lần nào hạ được đỉnh nên giữ bản chưa giảm; đỉnh vẫn"
        else:
            done = "đỉnh"
        return f"Âm lượng sau AAC: {done} {_vi(peak)} dBTP, chưa đạt trần {_vi(PEAK_DBTP)} dBTP."
    if trim > 0:
        return f"Âm lượng sau AAC: giảm thêm {_vi(trim)} dB để đỉnh ≤ {_vi(PEAK_DBTP)} dBTP."
    return None


def _vi(value: float) -> str:
    """One decimal, Vietnamese style: comma and a true minus sign."""
    return f"{value:.1f}".replace(".", ",").replace("-", "−")


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


__all__ = ["AAC_TRIM_MARGIN_DB", "AAC_TRIM_TRIES", "DubbingMaster", "RECIPE", "WORKFLOW", "aac_note", "aac_trim", "plan",
           "with_trim"]

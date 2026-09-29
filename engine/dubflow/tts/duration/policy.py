"""Duration-aware fitting before any strong time-stretch is attempted."""

from __future__ import annotations

from dataclasses import dataclass
import re


_TEXT = re.compile(r"[\x00-\x1f\x7f]")
I64_MAX = (1 << 63) - 1


def _ticks(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX or (positive and value == 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'non-negative'} signed 64-bit tick")
    return value


@dataclass(frozen=True)
class DurationFit:
    segment_id: str
    slot_start_ticks: int
    slot_end_ticks: int
    generated_duration_ticks: int
    fit_mode: str
    speed_ratio_milli: int
    rewritten_text: str | None = None
    rewritten_duration_ticks: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not self.segment_id or len(self.segment_id) > 256 or _TEXT.search(self.segment_id):
            raise ValueError("segment_id is invalid")
        _ticks(self.slot_start_ticks, "slot_start_ticks")
        _ticks(self.slot_end_ticks, "slot_end_ticks", positive=False)
        if self.slot_end_ticks <= self.slot_start_ticks:
            raise ValueError("duration slot must be positive")
        _ticks(self.generated_duration_ticks, "generated_duration_ticks", positive=True)
        if self.fit_mode not in {"native", "padded", "speed_adjusted", "rewrite_required"}:
            raise ValueError("fit mode is invalid")
        if type(self.speed_ratio_milli) is not int or not 100 <= self.speed_ratio_milli <= 3000:
            raise ValueError("speed ratio is outside the safe range")
        if self.rewritten_text is not None and (not self.rewritten_text or len(self.rewritten_text) > 16384 or _TEXT.search(self.rewritten_text)):
            raise ValueError("rewritten text is invalid")
        if self.rewritten_duration_ticks is not None:
            _ticks(self.rewritten_duration_ticks, "rewritten_duration_ticks", positive=True)


@dataclass(frozen=True)
class DurationPolicy:
    max_speed_ratio_milli: int = 1250

    def __post_init__(self) -> None:
        if type(self.max_speed_ratio_milli) is not int or not 1000 <= self.max_speed_ratio_milli <= 3000:
            raise ValueError("max speed ratio must be between 1000 and 3000 milli")

    def fit(self, segment_id: str, slot_start_ticks: int, slot_end_ticks: int, generated_duration_ticks: int, *, rewritten_text: str | None = None, rewritten_duration_ticks: int | None = None) -> DurationFit:
        _ticks(slot_start_ticks, "slot_start_ticks")
        _ticks(slot_end_ticks, "slot_end_ticks")
        _ticks(generated_duration_ticks, "generated_duration_ticks", positive=True)
        slot = slot_end_ticks - slot_start_ticks
        if slot <= 0:
            raise ValueError("duration slot must be positive")
        if rewritten_duration_ticks is not None:
            _ticks(rewritten_duration_ticks, "rewritten_duration_ticks", positive=True)
        ratio = (generated_duration_ticks * 1000 + slot - 1) // slot
        if generated_duration_ticks == slot:
            mode = "native"
            safe_ratio = 1000
        elif generated_duration_ticks < slot:
            mode = "padded"
            safe_ratio = 1000
        elif ratio <= self.max_speed_ratio_milli:
            mode = "speed_adjusted"
            safe_ratio = max(100, ratio)
        elif rewritten_duration_ticks is not None:
            rewrite_ratio = (rewritten_duration_ticks * 1000 + slot - 1) // slot
            if rewrite_ratio <= self.max_speed_ratio_milli:
                mode = "speed_adjusted"
                safe_ratio = max(100, rewrite_ratio)
            else:
                mode = "rewrite_required"
                safe_ratio = self.max_speed_ratio_milli
        else:
            mode = "rewrite_required"
            safe_ratio = self.max_speed_ratio_milli
        return DurationFit(segment_id, slot_start_ticks, slot_end_ticks, generated_duration_ticks, mode, safe_ratio, rewritten_text, rewritten_duration_ticks)


__all__ = ["DurationFit", "DurationPolicy"]

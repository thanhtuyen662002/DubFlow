"""Stable speaker-cluster to Vietnamese voice assignment.

The planner is backend-neutral. It produces a voice/track plan that a caller
can feed into the existing validated TTS adapter. No audio is mixed here, so
overlapping speakers remain independent tracks all the way to the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping, Sequence

from engine.dubflow.tts.duration.policy import DurationFit, DurationPolicy


_TEXT = re.compile(r"[\x00-\x1f\x7f]")
I64_MAX = (1 << 63) - 1
ROLES = {"speaker", "narrator", "offscreen", "unresolved"}


def _bounded(value: object, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or _TEXT.search(value):
        raise ValueError(f"{name} must be bounded text")
    return value


def _ticks(value: object, name: str) -> int:
    if type(value) is not int or value < 0 or value > I64_MAX:
        raise ValueError(f"{name} must be a non-negative signed 64-bit tick")
    return value


@dataclass(frozen=True)
class VoiceOption:
    voice_id: str
    version: str = "1"
    narrator: bool = False

    def __post_init__(self) -> None:
        _bounded(self.voice_id, "voice_id")
        _bounded(self.version, "version", 128)
        if type(self.narrator) is not bool:
            raise ValueError("narrator must be boolean")


@dataclass(frozen=True)
class TtsCue:
    segment_id: str
    speaker_cluster_id: str | None
    role: str
    start_ticks: int
    end_ticks: int
    text: str

    def __post_init__(self) -> None:
        _bounded(self.segment_id, "segment_id")
        _ticks(self.start_ticks, "start_ticks")
        _ticks(self.end_ticks, "end_ticks")
        if self.end_ticks <= self.start_ticks:
            raise ValueError("cue interval must be positive")
        if self.role not in ROLES:
            raise ValueError("cue role is invalid")
        if self.role == "speaker" and not self.speaker_cluster_id:
            raise ValueError("speaker cue requires a cluster id")
        if self.role != "speaker" and self.speaker_cluster_id is not None:
            raise ValueError("unresolved cue cannot carry a cluster id")
        _bounded(self.text, "text", 16384)


@dataclass(frozen=True)
class VoiceAssignment:
    segment_id: str
    track_id: str
    voice_id: str
    speaker_cluster_id: str | None

    def __post_init__(self) -> None:
        _bounded(self.segment_id, "segment_id")
        _bounded(self.track_id, "track_id")
        _bounded(self.voice_id, "voice_id")
        if self.speaker_cluster_id is not None:
            _bounded(self.speaker_cluster_id, "speaker_cluster_id")


@dataclass(frozen=True)
class MultiVoicePlan:
    assignments: tuple[VoiceAssignment, ...]
    durations: tuple[DurationFit, ...]

    def __post_init__(self) -> None:
        if len(self.assignments) != len(self.durations):
            raise ValueError("every cue must have one assignment and duration fit")
        segment_ids = [item.segment_id for item in self.assignments]
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("segment IDs must be unique in a voice plan")


class VoiceCaster:
    """Assign each cluster once and keep that voice for the whole plan."""

    def __init__(self, voices: Sequence[VoiceOption], *, fallback_voice: VoiceOption | None = None, duration_policy: DurationPolicy | None = None) -> None:
        if not voices:
            raise ValueError("at least one voice is required")
        if len({voice.voice_id for voice in voices}) != len(voices):
            raise ValueError("voice IDs must be unique")
        self.voices = tuple(voices)
        self.fallback_voice = fallback_voice or next((voice for voice in voices if voice.narrator), voices[0])
        if self.fallback_voice.voice_id not in {voice.voice_id for voice in voices}:
            raise ValueError("fallback voice must be in the voice list")
        self.duration_policy = duration_policy or DurationPolicy()

    def assign(self, cues: Iterable[TtsCue]) -> tuple[VoiceAssignment, ...]:
        ordered = sorted(tuple(cues), key=lambda cue: (cue.start_ticks, cue.end_ticks, cue.segment_id))
        clusters = sorted({cue.speaker_cluster_id for cue in ordered if cue.role == "speaker" and cue.speaker_cluster_id is not None})
        mapping = {cluster: self.voices[index % len(self.voices)] for index, cluster in enumerate(clusters)}
        assignments: list[VoiceAssignment] = []
        for cue in ordered:
            voice = mapping.get(cue.speaker_cluster_id, self.fallback_voice)
            # A voice may be reused when the installed voice pool is smaller
            # than the cast. Tracks still stay independent so overlapping
            # speakers are never flattened into one waveform.
            track_suffix = cue.speaker_cluster_id or "fallback"
            track = "track:" + voice.voice_id + ":" + track_suffix
            assignments.append(VoiceAssignment(cue.segment_id, track, voice.voice_id, cue.speaker_cluster_id))
        return tuple(assignments)

    def plan(self, cues: Sequence[TtsCue], generated_duration_ticks: Mapping[str, int], *, rewritten_text: Mapping[str, str] | None = None, rewritten_duration_ticks: Mapping[str, int] | None = None) -> MultiVoicePlan:
        cues = tuple(cues)
        assignments = self.assign(cues)
        by_id = {cue.segment_id: cue for cue in cues}
        if len(by_id) != len(cues):
            raise ValueError("cue segment IDs must be unique")
        durations: list[DurationFit] = []
        for assignment in assignments:
            cue = by_id[assignment.segment_id]
            if assignment.segment_id not in generated_duration_ticks:
                raise ValueError(f"missing generated duration for {assignment.segment_id}")
            durations.append(self.duration_policy.fit(cue.segment_id, cue.start_ticks, cue.end_ticks, generated_duration_ticks[cue.segment_id], rewritten_text=(rewritten_text or {}).get(cue.segment_id), rewritten_duration_ticks=(rewritten_duration_ticks or {}).get(cue.segment_id)))
        return MultiVoicePlan(assignments, tuple(durations))


__all__ = ["MultiVoicePlan", "TtsCue", "VoiceAssignment", "VoiceCaster", "VoiceOption"]

"""Bounded per-cue duration ladder, with separate spoken-text evidence.

This is not a general semantic rewriter. It never selects a model hypothesis,
removes arbitrary words, or modifies B1 translation/source timing. A failed
rewrite retains the normal per-cue refusal and usable B1/source fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence

from engine.dubflow.tts import TtsCheckpoint, TtsError, TtsInput
from engine.dubflow.tts.adapter import FAST_FIT_MAX_MILLI
from .export_publication import plain


RECIPE = "vi-safe-condensation-v2-window4000-fast1500"
RULE = "first-time-adverb"
WARNING = "TTS_DURATION_REWRITE_APPLIED: "
MAX_RECORD_BYTES = 65536
_QUOTES = frozenset('\"\'“”‘’«»`')
_RULES = (
    ("actually-filler", "thực ra thì", "thực ra"),
    ("copula-filler", r"thì là(?= \w)", "là"),
    ("discourse-filler", "à thì|ừm|ờ|kiểu như", ""),
    (RULE, "lần đầu tiên", "lần đầu"),
    ("now-adverb", "bây giờ", "giờ"),
    ("how-adverb", "như thế nào", "thế nào"),
    ("because-connective", "tại vì", "vì"),
    ("but-connective", "nhưng mà", "nhưng"),
    ("so-connective", "cho nên", "nên"),
    ("very-intensifier", "rất là", "rất"),
    ("indefinite-filler", r"một cái(?= (?:gì đó|cách|kiểu|cảm giác)(?!\w))", "cái"),
)
_FINAL = re.compile(r"(?<!\w)(?:à|ạ|nhé|nha|đấy|đó|vậy|thế|mà|chứ|cơ|hả|nhỉ)(?=[.!?,;…]*$)")
_COMPLEMENT_ENDINGS = frozenset((
    "ở tại đến tới từ là như làm nói cái điều người gì nào sao vì không chẳng chưa đừng "
    "nghĩ biết thấy muốn cần chọn giữ lấy mua có được bằng về cũng vẫn phải đúng chính tên "
    "trên dưới sau trước bên ngoài trong chỗ nơi hôm lúc khi ngày ai đâu cứ "
    "thay tư ưu lợi xu gia quyền tình trần"
).split())
_CO_COMPOUNDS = frozenset("động nguy hữu vô phi thời thừa căn nương đầu quân".split())


def _encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def recipe_hash() -> str:
    return "sha256:" + sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class RewritePlan:
    original_text: str
    spoken_text: str
    rules: tuple[str, ...]


def plan_rewrite(text: str) -> RewritePlan | None:
    """Apply reviewed lexical contractions once; decline names and quotations."""
    if not isinstance(text, str) or not 1 <= len(text) <= 512:
        return None
    if any(char in _QUOTES or ord(char) < 32 or ord(char) == 127 for char in text):
        return None
    words = list(re.finditer(r"[^\W\d_]+", text))
    if any(word.group()[0].isupper() for word in words[1:]):
        return None
    spoken, rules = text, []
    for name, phrase, replacement in _RULES:
        pattern = re.compile(r"(?<!\w)(?:" + phrase + r")(?!\w)", re.IGNORECASE)
        def contract(match):
            value = replacement
            return value.capitalize() if value and match.group()[0].isupper() else value
        changed = pattern.sub(contract, spoken)
        if changed != spoken:
            rules.append(name)
            spoken = changed
    # Locative/demonstrative complements and predicates are not particles.
    while (match := _FINAL.search(spoken)) is not None:
        before = spoken[:match.start()].rstrip()
        previous = before.rsplit(maxsplit=1)[-1].casefold() if before else ""
        if (not before or match.group() in {"đó", "đấy", "vậy", "thế"} and previous in _COMPLEMENT_ENDINGS
                or match.group() == "cơ" and previous in _CO_COMPOUNDS):
            break
        if "final-particle" not in rules:
            rules.append("final-particle")
        spoken = before + spoken[match.end():]
    spoken = re.sub(r"\s+([,.!?;:…])", r"\1", " ".join(spoken.split())).lstrip(",; ")
    if not spoken or not re.search(r"\w", spoken) or spoken == text or not rules:
        return None
    return RewritePlan(text, spoken, tuple(rules))


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate spoken-text receipt field")
        value[key] = item
    return value


def _atomic(path: Path, payload: bytes) -> None:
    plain(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".spoken-", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SpokenTextStore:
    """Per-cue evidence committed after waveform validation, before checkpoint.

    A missing/corrupt/mismatched receipt invalidates only that cue's reusable
    checkpoint. The public TTS checkpoint/document schemas remain unchanged.
    """
    def __init__(self, audio_directory: Path, *, identity: str, segments: Sequence[TtsInput]):
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("invalid spoken-text generation identity")
        self.directory = audio_directory.absolute() / "spoken_text"
        plain(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.recipe_hash = recipe_hash()
        self.segments = {item.segment_id: item for item in segments}
        if len(self.segments) != len(segments):
            raise ValueError("duplicate spoken-text source identity")
        self.pending: dict[str, RewritePlan] = {}
        self.records: dict[str, dict] = {}
        self.warnings: list[str] = []

    def _path(self, identifier: str) -> Path:
        return self.directory / (sha256(identifier.encode("utf-8")).hexdigest() + ".json")

    def _fields(self, checkpoint: TtsCheckpoint, plan: RewritePlan | None) -> dict:
        segment = self.segments[checkpoint.segment_id]
        return _fields(segment, checkpoint.artifact_hash, checkpoint.artifact.content_hash,
                       self.identity, self.recipe_hash, plan, checkpoint.artifact.to_dict())

    def _plan_for_artifact(self, checkpoint: TtsCheckpoint) -> RewritePlan | None:
        markers = [warning for warning in checkpoint.artifact.warnings if warning.startswith(WARNING)]
        if not markers:
            return None
        plan = plan_rewrite(self.segments[checkpoint.segment_id].text)
        if plan is None:
            raise ValueError("rewritten waveform has no applicable lexical rule")
        if markers != [rewrite_warning(checkpoint.segment_id, plan.rules)]:
            raise ValueError("unrecognized spoken-text rewrite advisory")
        return plan

    def filter_checkpoints(self, checkpoints: Mapping[str, TtsCheckpoint]) -> dict[str, TtsCheckpoint]:
        result = {}
        for identifier, checkpoint in checkpoints.items():
            try:
                if identifier != checkpoint.segment_id:
                    raise ValueError("checkpoint key differs from its cue")
                path = self._path(identifier)
                plain(path)
                with path.open("rb") as stream:
                    payload = stream.read(MAX_RECORD_BYTES + 1)
                if len(payload) > MAX_RECORD_BYTES:
                    raise ValueError("spoken-text receipt exceeds its bound")
                record = json.loads(payload, object_pairs_hook=_unique)
                expected = self._fields(checkpoint, self._plan_for_artifact(checkpoint))
                if _encoded(record) != _encoded({**expected, "record_hash": sha256(_encoded(expected)).hexdigest()}):
                    raise ValueError("spoken-text receipt differs from the current input/recipe/waveform")
                self.records[identifier] = expected
                result[identifier] = checkpoint
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                self.warnings.append("TTS_SPOKEN_TEXT_CHECKPOINT_INVALID: " + identifier)
        return result

    def commit(self, checkpoint: TtsCheckpoint) -> None:
        plan = self._plan_for_artifact(checkpoint)
        if plan != self.pending.get(checkpoint.segment_id):
            raise ValueError("spoken-text decision differs from the validated synthesis")
        fields = self._fields(checkpoint, plan)
        payload = _encoded({**fields, "record_hash": sha256(_encoded(fields)).hexdigest()})
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError("spoken-text receipt exceeds its bound")
        _atomic(self._path(checkpoint.segment_id), payload)
        self.records[checkpoint.segment_id] = fields

    def document(self, tts_document) -> dict:
        entries = []
        for artifact in tts_document.artifacts:
            checkpoint = TtsCheckpoint(artifact.segment_id, artifact.artifact_hash, artifact)
            expected = self._fields(checkpoint, self._plan_for_artifact(checkpoint))
            if self.records.get(artifact.segment_id) != expected:
                raise ValueError("accepted waveform lacks matching spoken-text evidence")
            entries.append(expected)
        return {"schema_version": 1, "kind": "dubbing_spoken_text_document", "recipe": RECIPE,
                "recipe_hash": self.recipe_hash, "checkpoint_identity": self.identity,
                "tts_document_hash": "sha256:" + sha256(tts_document.to_bytes()).hexdigest(),
                "provenance": tts_document.provenance.to_dict(),
                "source_segment_ids": list(tts_document.source_segment_ids), "entries": entries,
                "fidelity_evidence": "restricted-lexical-rule;semantic-confidence-not-calibrated"}


def rewrite_warning(identifier: str, rules: tuple[str, ...] = (RULE,)) -> str:
    return WARNING + identifier + "; " + RECIPE + "; rules " + ",".join(rules) + "; original translation retained; review spoken text"


def _fields(segment, artifact_hash, audio_hash, identity, digest, plan, artifact):
    spoken = segment.text if plan is None else plan.spoken_text
    return {"schema_version": 1, "kind": "dubbing_spoken_text", "recipe": RECIPE,
            "recipe_hash": digest, "checkpoint_identity": identity,
            "original_input": segment.to_dict(), "mode": "original" if plan is None else "rewritten",
            "spoken_text": spoken, "spoken_text_hash": "sha256:" + sha256(spoken.encode("utf-8")).hexdigest(),
            "rules": [] if plan is None else list(plan.rules),
            "fit": {"render_window_end": artifact.get("render_window_end"),
                    "speed_ratio_milli": artifact["speed_ratio_milli"],
                    "warnings": [item for item in artifact["warnings"] if item.startswith(("TTS_RENDER_WINDOW_WIDENED:", "TTS_FAST_FIT:"))]},
            "artifact_hash": artifact_hash, "audio_hash": audio_hash}


def validate_spoken_text_document(value: dict, speech_bytes: bytes,
                                  segments: Sequence[TtsInput], *, identity: str) -> None:
    """Read-only source/input/provenance validation for release/export readers."""
    from engine.dubflow.tts import parse_tts_json
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("invalid spoken-text checkpoint identity")
    speech = parse_tts_json(speech_bytes)
    sources = {segment.segment_id: segment for segment in segments}
    if len(sources) != len(segments) or set(sources) != set(speech["source_segment_ids"]):
        raise ValueError("spoken-text source cues differ")
    entries = []
    digest = recipe_hash()
    for artifact in speech["artifacts"]:
        segment = sources[artifact["segment_id"]]
        markers = [warning for warning in artifact["warnings"] if warning.startswith(WARNING)]
        plan = None
        if markers:
            plan = plan_rewrite(segment.text)
            if plan is None:
                raise ValueError("rewritten speech has no applicable rule")
            if markers != [rewrite_warning(segment.segment_id, plan.rules)]:
                raise ValueError("unrecognized spoken-text rewrite advisory")
        entries.append(_fields(segment, artifact["artifact_hash"], artifact["content_hash"], identity, digest, plan, artifact))
    expected = {"schema_version": 1, "kind": "dubbing_spoken_text_document", "recipe": RECIPE,
                "recipe_hash": digest, "checkpoint_identity": identity,
                "tts_document_hash": "sha256:" + sha256(speech_bytes).hexdigest(),
                "provenance": speech["provenance"], "source_segment_ids": speech["source_segment_ids"],
                "entries": entries, "fidelity_evidence": "restricted-lexical-rule;semantic-confidence-not-calibrated"}
    if _encoded(value) != _encoded(expected):
        raise ValueError("spoken-text document differs from the source/input/recipe/accepted waveform")


class BoundedDurationRewriteEngine:
    def __init__(self, engine, store: SpokenTextStore, *, render_window_limits=None):
        self.engine = engine
        self.store = store
        self.rewritten_requests: set[str] = set()
        self.render_window_limits = dict(render_window_limits or {})

    def capabilities(self):
        return self.engine.capabilities()

    def healthcheck(self, voice):
        return self.engine.healthcheck(voice)

    def close(self):
        close = getattr(self.engine, "close", None)
        if close is not None:
            close()

    def synthesize(self, request):
        if request.segment != self.store.segments.get(request.segment.segment_id):
            raise TtsError("TTS_SPOKEN_TEXT_INPUT_INVALID", "synthesis input differs from the bound source cue")
        identifier = request.segment.segment_id
        plan, warnings = None, ()
        current = request
        limit = self.render_window_limits.get(identifier)
        for tier in range(4):
            if tier == 1:
                if limit is None or request.segment.render_window_end is None or limit.ticks <= request.segment.render_window_end.ticks:
                    continue
                current = replace(current, segment=replace(current.segment, render_window_end=limit))
                warnings += (f"TTS_RENDER_WINDOW_WIDENED: {identifier}; end {limit.ticks}",)
            elif tier == 2:
                candidate = plan_rewrite(request.segment.text)
                if candidate is None or request.request_id in self.rewritten_requests:
                    continue
                plan = candidate
                self.rewritten_requests.add(request.request_id)
                current = replace(current, segment=replace(current.segment, text=plan.spoken_text, normalized_text=None))
                warnings += (rewrite_warning(identifier, plan.rules),)
            elif tier == 3:
                current = replace(current, config=replace(current.config, max_speed_ratio_milli=FAST_FIT_MAX_MILLI))
            try:
                result = self.engine.synthesize(current)
            except TtsError as error:
                if tier == 0 and error.code != "DURATION_FIT_REQUIRED":
                    raise
                if error.code != "DURATION_FIT_REQUIRED" or tier == 3:
                    evidence = f"TTS_DURATION_FIT_REFUSED: {identifier}; " + "; ".join(warnings)
                    if tier == 3:
                        evidence += f"; max speed {FAST_FIT_MAX_MILLI}"
                    self.store.warnings.append(evidence)
                    raise TtsError(error.code, error.condition + "; " + evidence, retryable=False) from error
                continue
            if tier == 3:
                warnings += (f"TTS_FAST_FIT: {identifier}; speed {result.speed_ratio_milli}",)
            self.store.pending[identifier] = plan
            return replace(result, warnings=result.warnings + warnings,
                           render_window_end=current.segment.render_window_end)

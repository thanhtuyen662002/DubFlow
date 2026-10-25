"""Experimental within-job CAM++ speaker clustering and deterministic casting.

Native dependencies are lazy: importing this module and voice casting/planning
work in the numpy-free governance runtime. Input PCM is signed little-endian
16-bit, 16 kHz mono, aligned to the app's canonical source-audio origin.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import heapq
import json
import math
import os
from pathlib import Path
from statistics import median
import tempfile
from typing import Any, Mapping, Sequence
import wave

SAMPLE_RATE = 16000
MIN_EMBED_MS = 800
MAX_SPEAKERS = 8
# Bound feature extraction even for unusually long sidecar cues.
EMBED_WINDOW_SAMPLES = 10 * SAMPLE_RATE


@dataclass(frozen=True)
class SpeakerResult:
    labels: dict[str, str]
    speakers: list[dict[str, Any]]


def fbank(samples):
    """Spike-compatible Kaldi fbank: Povey25ms/shift10ms, preemphasis, 80 mel."""
    import numpy as np

    samples = np.asarray(samples)
    frame, shift, n_fft = 400, 160, 512
    if len(samples) < frame:
        return np.zeros((0, 80), np.float32)
    count = 1 + (len(samples) - frame) // shift
    indices = np.arange(frame)[None, :] + shift * np.arange(count)[:, None]
    frames = samples[indices].astype(np.float64)
    frames -= frames.mean(1, keepdims=True)
    frames[:, 1:] -= .97 * frames[:, :-1]
    frames[:, 0] -= .97 * frames[:, 0]
    window = (.5 - .5 * np.cos(2 * np.pi * np.arange(frame) / (frame - 1))) ** .85
    spectrum = np.abs(np.fft.rfft(frames * window, n_fft)) ** 2
    mel = lambda f: 1127.0 * np.log(1 + f / 700.0)
    centers = np.linspace(mel(20), mel(SAMPLE_RATE / 2), 82)
    frequencies = mel(np.arange(n_fft // 2 + 1) * SAMPLE_RATE / n_fft)
    banks = np.zeros((80, n_fft // 2 + 1), np.float32)
    for index in range(80):
        left, center, right = centers[index:index + 3]
        banks[index] = np.maximum(0, np.minimum((frequencies - left) / (center - left),
                                               (right - frequencies) / (right - center)))
    return np.log(np.maximum(spectrum @ banks.T.astype(np.float64), np.finfo(np.float32).eps)).astype(np.float32)


def embed(session, samples):
    import numpy as np

    features = fbank(samples)
    if not len(features):
        raise ValueError("speaker utterance has no feature frames")
    features -= features.mean(0, keepdims=True)
    vector = np.asarray(session.run(None, {"x": features[None]})[0][0], dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if vector.ndim != 1 or not np.isfinite(vector).all() or norm <= 1e-9:
        raise ValueError("speaker embedding is empty or non-finite")
    return vector / norm


def _unit(vector) -> list[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if not values or not math.isfinite(norm) or norm <= 1e-9:
        raise ValueError("invalid speaker vector")
    return [value / norm for value in values]


def cluster(vectors, threshold: float = .3) -> list[int]:
    """Average-linkage cosine similarity (not cosine distance); stable tie order.

    Weighted linkage updates avoid rescanning every member pair per merge.
    Planning is pure Python, O(n²) space and O(n² log n) time.
    """
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("speaker threshold must be a cosine similarity")
    units = [_unit(vector) for vector in vectors]
    if units and any(len(vector) != len(units[0]) for vector in units):
        raise ValueError("speaker vector dimensions differ")
    groups = {index: [index] for index in range(len(units))}
    similarities, pairs = {}, []
    for a in groups:
        for b in range(a + 1, len(units)):
            score = sum(x * y for x, y in zip(units[a], units[b]))
            similarities[a, b] = score
            pairs.append((-score, a, b))
    heapq.heapify(pairs)
    next_id = len(units)
    while pairs:
        negative, a, b = heapq.heappop(pairs)
        if a not in groups or b not in groups:
            continue
        if -negative < threshold:
            break
        left, right = groups.pop(a), groups.pop(b)
        for other in groups:
            score = (len(left) * similarities.pop(tuple(sorted((a, other))))
                     + len(right) * similarities.pop(tuple(sorted((b, other))))) / (len(left) + len(right))
            similarities[other, next_id] = score
            heapq.heappush(pairs, (-score, other, next_id))
        similarities.pop((a, b), None)
        groups[next_id] = left + right
        next_id += 1
    labels = [0] * len(units)
    for label, members in enumerate(sorted(groups.values(), key=min)):
        for index in members:
            labels[index] = label
    return labels


def estimate_pitch_hz(samples) -> float | None:
    """Median voiced-frame autocorrelation F0 in 60–400 Hz; silence is unknown."""
    import numpy as np

    values = np.asarray(samples, dtype=np.float64)
    frame, shift = 800, 320
    lo, hi = SAMPLE_RATE // 400, math.ceil(SAMPLE_RATE / 60)
    pitches = []
    for start in range(0, len(values) - frame + 1, shift):
        signal = values[start:start + frame].copy()
        signal -= signal.mean()
        if np.sqrt(np.mean(signal * signal)) < .003:
            continue
        # Normalize by overlapping energy, avoiding a bias toward short lags.
        correlation = np.correlate(signal, signal, mode="full")[frame - 1:]
        energy = np.concatenate(([0.0], np.cumsum(signal * signal)))
        # Include neighboring lags for peak interpolation at 60 and 400 Hz.
        lags = np.arange(lo - 1, hi + 2)
        scores = correlation[lags] / np.sqrt(np.maximum(energy[frame - lags] * (energy[frame] - energy[lags]), 1e-20))
        peaks = [index for index in range(1, len(scores) - 1)
                 if scores[index] >= scores[index - 1] and scores[index] > scores[index + 1]]
        if not peaks:
            continue
        best = max(float(scores[index]) for index in peaks)
        # First comparable peak avoids choosing an octave-down periodic repeat.
        chosen = next((index for index in peaks if scores[index] >= max(.6, best * .98)), None)
        if chosen is None:
            continue
        lag = float(lags[chosen])
        a, b, c = scores[chosen - 1:chosen + 2]
        if a - 2 * b + c:
            lag += float(.5 * (a - c) / (a - 2 * b + c))
        pitches.append(min(400.0, max(60.0, SAMPLE_RATE / lag)))
    return float(median(pitches)) if pitches else None


def gender_guess(f0: float | None) -> str:
    if f0 is None or not math.isfinite(f0) or f0 <= 0:
        return "unknown"
    return "male" if f0 < 155 else "female" if f0 > 175 else "unknown"


def _load_session(model_path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])


def _span(cue) -> tuple[str, int, int]:
    get = cue.get if isinstance(cue, Mapping) else lambda key: getattr(cue, key)
    identifier, start, end = get("cue_id"), get("start_ms"), get("end_ms")
    if not isinstance(identifier, str) or not identifier or type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError("invalid speaker cue span")
    return identifier, start, end


def _save_chunk(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, suffix=".partial")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            digest = sha256(json.dumps(value, allow_nan=False, sort_keys=True).encode()).hexdigest()
            json.dump({**value, "sha256": digest}, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def assign_speakers(audio_path_16k_mono_wav_or_pcm, cues, model_path, *, threshold=.3,
                    checkpoint_dir: Path | None = None) -> SpeakerResult:
    """Assign audio clusters; checkpoint_dir must be bound to source/model/recipe.

    Read each cue by integer sample offset, never loading an entire media file.
    Long utterances use bounded 10-second windows and a mean normalized vector.
    """
    import numpy as np

    spans = [_span(cue) for cue in cues]
    if len({item[0] for item in spans}) != len(spans):
        raise ValueError("duplicate speaker cue ID")
    if not spans:
        return SpeakerResult({}, [])
    path = Path(audio_path_16k_mono_wav_or_pcm)
    with path.open("rb") as header:
        is_wav = header.read(4) == b"RIFF"
    reader = wave.open(str(path), "rb") if is_wav else path.open("rb")
    session, vectors, embedded, pitches = None, [], [], {}
    try:
        if is_wav and (reader.getframerate(), reader.getnchannels(), reader.getsampwidth(), reader.getcomptype()) != (SAMPLE_RATE, 1, 2, "NONE"):
            raise ValueError("speaker input must be 16 kHz mono signed-16 PCM")
        for index, (identifier, start, end) in enumerate(spans):
            if end - start < MIN_EMBED_MS:
                continue
            cached = None
            chunk_path = None
            if checkpoint_dir is not None:
                chunk_path = Path(checkpoint_dir) / (sha256(json.dumps([identifier, start, end]).encode()).hexdigest() + ".json")
                try:
                    cached = json.loads(chunk_path.read_text(encoding="utf-8"))
                    digest = cached.pop("sha256")
                    if sha256(json.dumps(cached, allow_nan=False, sort_keys=True).encode()).hexdigest() != digest:
                        raise ValueError("speaker chunk checksum mismatch")
                    _unit(cached["vector"])
                    if cached["f0_hz"] is not None and not 60 <= float(cached["f0_hz"]) <= 400:
                        raise ValueError("invalid cached pitch")
                except (OSError, ValueError, KeyError, TypeError):
                    cached = None
            if cached is None:
                if session is None:
                    session = _load_session(model_path)
                begin, finish = start * 16, end * 16
                pieces, f0s = [], []
                for offset in range(begin, finish, EMBED_WINDOW_SAMPLES):
                    count = min(EMBED_WINDOW_SAMPLES, finish - offset)
                    if is_wav:
                        reader.setpos(offset)
                        raw = reader.readframes(count)
                    else:
                        reader.seek(offset * 2)
                        raw = reader.read(count * 2)
                    if len(raw) != count * 2:
                        raise ValueError("speaker cue extends beyond decoded audio")
                    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                    if count >= MIN_EMBED_MS * 16:
                        pieces.append(embed(session, samples))
                    pitch = estimate_pitch_hz(samples)
                    if pitch is not None:
                        f0s.append(pitch)
                cached = {"vector": _unit(np.mean(pieces, axis=0)), "f0_hz": median(f0s) if f0s else None}
                if chunk_path is not None:
                    _save_chunk(chunk_path, cached)
            embedded.append(index)
            vectors.append(_unit(cached["vector"]))
            pitches[index] = cached["f0_hz"]
    finally:
        reader.close()
    groups: dict[int, list[int]] = {}
    for index, label in zip(embedded, cluster(vectors, threshold)):
        groups.setdefault(label, []).append(index)
    label_by_index = {index: key for key, members in groups.items() for index in members}
    if not groups:
        groups[0] = []
    for index, (_, start, end) in enumerate(spans):
        if index in label_by_index:
            continue
        # Distance between intervals; ties resolve by midpoint and source order.
        nearest = min(embedded, key=lambda i: (max(0, spans[i][1] - end, start - spans[i][2]),
                      abs((start + end) - (spans[i][1] + spans[i][2])), i)) if embedded else None
        key = label_by_index[nearest] if nearest is not None else 0
        groups[key].append(index)
    # Rank cluster size using all speech, including borrowed short cues. Only
    # embedded members contribute evidence to the average cosine similarity.
    vector_by_index = dict(zip(embedded, vectors))
    while len(groups) > MAX_SPEAKERS:
        smallest = min(groups, key=lambda key: (sum(spans[i][2] - spans[i][1] for i in groups[key]), min(groups[key])))
        members = groups.pop(smallest)
        left = [vector_by_index[i] for i in members if i in vector_by_index]
        def similarity(key):
            right = [vector_by_index[i] for i in groups[key] if i in vector_by_index]
            return sum(sum(a * b for a, b in zip(x, y)) for x in left for y in right) / (len(left) * len(right))
        target = max(groups, key=lambda key: (similarity(key), -min(groups[key])))
        groups[target].extend(members)
    ordered = sorted(groups.values(), key=lambda members: (-sum(spans[i][2] - spans[i][1] for i in members), min(members)))
    labels, speakers = {}, []
    for number, members in enumerate(ordered, 1):
        identifier = f"S{number}"
        members.sort()
        for index in members:
            labels[spans[index][0]] = identifier
        f0s = [pitches[i] for i in members if pitches.get(i) is not None]
        f0 = float(median(f0s)) if f0s else None
        speakers.append({"id": identifier, "cues": [spans[i][0] for i in members],
                         "seconds": sum(spans[i][2] - spans[i][1] for i in members) / 1000,
                         "gender": gender_guess(f0), "f0_hz": f0})
    return SpeakerResult(labels, speakers)


def cast_voices(speakers: Sequence[Mapping[str, Any]], catalog_voices: Sequence[Mapping[str, Any]],
                primary_voice_id: str, overrides: Mapping[str, str]) -> dict[str, str]:
    """Catalog order breaks ties; reserve all overrides before automatic casting."""
    catalog = {voice["voice_id"]: voice for voice in catalog_voices}
    if primary_voice_id not in catalog:
        raise ValueError("primary voice is absent from the catalog")
    ordered = sorted(speakers, key=lambda speaker: int(speaker["id"][1:]))
    result = {speaker["id"]: overrides[speaker["id"]] for speaker in ordered
              if overrides.get(speaker["id"]) in catalog}
    if any(speaker["id"] == "S1" for speaker in ordered):
        result.setdefault("S1", primary_voice_id)
    gender_names = {"male": "nam", "female": "nữ"}
    for speaker in ordered:
        identifier = speaker["id"]
        if identifier in result:
            continue
        available = [key for key in catalog if key not in result.values()] or list(catalog)
        wanted = gender_names.get(speaker.get("gender"))
        matching = [key for key in available if wanted and str(catalog[key].get("gender", "")).casefold() == wanted]
        result[identifier] = (matching or available)[0]
    return {speaker["id"]: result[speaker["id"]] for speaker in ordered}

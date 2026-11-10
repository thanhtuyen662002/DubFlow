"""Natural speech kept per sentence on disk, so a rerun or a one-sentence edit only speaks what changed.

The key is the voice's identity (engine, pinned model profile, inference recipe, voice, speed) plus the sentence
text: a different model or recipe never reuses old audio. Audio is stored as the 16-bit mono WAV the narration is
written in anyway (``product.render.write_narration`` rounds to 16 bits), so a cached sentence gives the same
narration bytes as a fresh one of the same audio. The engine is started only on the first miss: a run whose
sentences are all cached never loads the voice model.
"""
from __future__ import annotations

from array import array
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence
import wave

SCHEMA = 1
LIMIT_BYTES = 512 * 1024 * 1024  # oldest sentences are dropped past this
MAX_TEXT = 4000


class SentenceCache:
    def __init__(self, root: Path | str, identity: Mapping[str, Any], sample_rate: int, *, limit_bytes: int = LIMIT_BYTES) -> None:
        self.root = Path(root)
        self.identity = json.dumps({"schema_version": SCHEMA, **identity}, ensure_ascii=False, sort_keys=True)
        self.sample_rate = sample_rate
        self.limit_bytes = limit_bytes
        self.hits = self.misses = 0

    def path(self, text: str) -> Path:
        key = hashlib.sha256(json.dumps([self.identity, text.strip()], ensure_ascii=False).encode("utf-8")).hexdigest()
        return self.root / key[:2] / f"{key}.wav"

    def get(self, text: str) -> list[float] | None:
        path = self.path(text)
        try:
            with wave.open(str(path), "rb") as reader:
                if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getframerate() != self.sample_rate:
                    return None
                frames = reader.getnframes()
                data = reader.readframes(frames)
        except (OSError, EOFError, wave.Error):
            return None
        if frames <= 0 or len(data) != frames * 2:  # cut off: speak again
            return None
        values = array("h")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        try:
            os.utime(path)  # recently used survives pruning
        except OSError:
            pass
        return [value / 32767 for value in values]

    def put(self, text: str, samples: Sequence[float]) -> None:
        if not samples or len(text) > MAX_TEXT:
            return
        path = self.path(text)
        partial = path.with_name(path.name + ".partial")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            values = array("h", (max(-32768, min(32767, round(sample * 32767))) for sample in samples))
            if sys.byteorder != "little":
                values.byteswap()
            with wave.open(str(partial), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(self.sample_rate)
                writer.writeframes(values.tobytes())
            os.replace(partial, path)
        except OSError:  # a full or read-only disk only costs speed
            try:
                partial.unlink()
            except OSError:
                pass

    def prune(self) -> None:
        """Drop the least recently used sentences until the cache fits its limit."""
        try:
            files = [(p.stat().st_mtime, p.stat().st_size, p) for p in self.root.glob("??/*.wav")]
        except OSError:
            return
        total = sum(size for _, size, _ in files)
        for _, size, path in sorted(files):
            if total <= self.limit_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass


class CachedSpeaker:
    """A narration ``Speaker`` that answers from ``cache`` and starts the real one (``start()``) only on a miss."""

    def __init__(self, start: Callable[[], Any], cache: SentenceCache) -> None:
        self._start, self.cache, self._voice = start, cache, None
        self.sample_rate = cache.sample_rate
        cache.prune()

    def speak(self, text: str) -> Sequence[float]:
        found = self.cache.get(text)
        if found is not None:
            self.cache.hits += 1
            return found
        self.cache.misses += 1
        if self._voice is None:
            self._voice = self._start()
            if self._voice.sample_rate != self.cache.sample_rate:  # not what the profile pins: no caching
                self.sample_rate = self._voice.sample_rate
        samples = self._voice.speak(text)
        if self._voice.sample_rate == self.cache.sample_rate:
            self.cache.put(text, samples)
        return samples

    def close(self) -> None:
        if self._voice is not None:
            self._voice.close()

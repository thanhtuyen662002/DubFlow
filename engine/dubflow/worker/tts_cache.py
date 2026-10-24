"""Reuse validated speech across B2 generations of the same output folder.

A reviewed translation changes the generation identity, so every per-cue
checkpoint (bound to the whole document) is discarded. This cache sits under
the adapter at the engine boundary: identical engine requests (same text,
timing, render window, voice, config and recipe) return the waveform an
earlier generation already produced and the adapter accepted. The adapter
still validates, hashes and publishes every artifact as usual, so the cache
only skips inference. Failures are never cached.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

from engine.dubflow.tts import EngineSynthesis
from .export_publication import plain

SCHEMA_VERSION = 1
MAX_ENTRY_BYTES = 128 * 1024 * 1024
_FIELDS = ("format", "sample_rate", "channels", "bits_per_sample", "fit_mode", "speed_ratio_milli", "warnings")


class CachedSynthesisEngine:
    def __init__(self, engine, directory: Path, *, identity: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("synthesis cache identity is invalid")
        self.engine = engine
        self.directory = Path(directory).absolute()
        plain(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.hits = 0

    def capabilities(self):
        return self.engine.capabilities()

    def healthcheck(self, voice):
        return self.engine.healthcheck(voice)

    def close(self):
        close = getattr(self.engine, "close", None)
        if close is not None:
            close()

    def _key(self, request) -> str:
        value = {"identity": self.identity, "segment": request.segment.to_dict(), "voice": request.voice.content_hash(),
                 "config": request.config.content_hash(), "samples": [request.sample_start, request.sample_end]}
        return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _load(self, key: str) -> EngineSynthesis | None:
        meta_path, audio_path = self.directory / f"{key}.json", self.directory / f"{key}.wav"
        try:
            plain(meta_path)
            plain(audio_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or meta.get("schema_version") != SCHEMA_VERSION or meta.get("key") != key:
                return None
            if audio_path.stat().st_size > MAX_ENTRY_BYTES:
                return None
            audio = audio_path.read_bytes()
            if "sha256:" + sha256(audio).hexdigest() != meta.get("audio_sha256"):
                return None
            fields = {name: meta[name] for name in _FIELDS}
            fields["warnings"] = tuple(fields["warnings"])
            return EngineSynthesis(audio, **fields)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _store(self, key: str, synthesis: EngineSynthesis) -> None:
        if len(synthesis.audio_bytes) > MAX_ENTRY_BYTES:
            return
        meta = {"schema_version": SCHEMA_VERSION, "key": key, "audio_sha256": "sha256:" + sha256(synthesis.audio_bytes).hexdigest(),
                **{name: getattr(synthesis, name) for name in _FIELDS}}
        meta["warnings"] = list(meta["warnings"])
        # Audio first, metadata last: metadata is the commit marker.
        for suffix, payload in ((".wav", synthesis.audio_bytes), (".json", json.dumps(meta, sort_keys=True).encode("utf-8"))):
            descriptor, temporary = tempfile.mkstemp(prefix=".cache-", suffix=".partial", dir=self.directory)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.directory / f"{key}{suffix}")
            finally:
                Path(temporary).unlink(missing_ok=True)

    def synthesize(self, request) -> EngineSynthesis:
        key = self._key(request)
        cached = self._load(key)
        if cached is not None:
            self.hits += 1
            return cached
        synthesis = self.engine.synthesize(request)
        try:
            self._store(key, synthesis)
        except OSError:
            pass  # A cache write failure must not fail synthesis.
        return synthesis

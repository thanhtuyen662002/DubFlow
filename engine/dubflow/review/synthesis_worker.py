"""Checkpointable single-cue candidates; only the supervisor can publish a graph.

The request and filesystem roots come from an admitted supervisor command.
This worker never opens SQLite, rewrites the previous export or grants a lease.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable, Iterator

if __package__ in (None, ""):
    # -S excludes system site initialization. Load only this executable's
    # bundled dependencies, ahead of the app's top-level packaging namespace.
    sys.path.append(str(Path(sys.executable).resolve().parent / "Lib" / "site-packages"))
    sys.path.append(str(Path(__file__).resolve().parents[3]))

from engine.dubflow.tts import LocalTtsAdapter, ResourceProfile, TtsConfig, TtsDocument
from engine.dubflow.tts import vieneu
from engine.dubflow.worker.b2_audio import B2_TTS_PRODUCER_VERSION, tts_recipe_identity
from engine.dubflow.worker.export_publication import plain
from engine.dubflow.worker.tts_checkpoints import TtsCheckpointStore
from engine.dubflow.worker.tts_rewrites import BoundedDurationRewriteEngine, SpokenTextStore, validate_spoken_text_document
from engine.dubflow.review.production import ReviewError
from engine.dubflow.review.synthesis_input import MAX_REQUEST_BYTES, CueSynthesisInput, read_cue_synthesis_input


@dataclass(frozen=True)
class CueCandidate:
    document: TtsDocument
    document_path: Path
    spoken_text_path: Path


@contextmanager
def _writer(generation: Path) -> Iterator[None]:
    path = generation / "writer.lock"
    plain(path)
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        locked = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as error:
                raise ReviewError("cue candidate writer is busy; retry after its owner exits") from error
            yield
        finally:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic(path: Path, raw: bytes) -> None:
    plain(path)
    descriptor, name = tempfile.mkstemp(prefix=".cue-", suffix=".partial", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def production_cue_config() -> TtsConfig:
    return TtsConfig(sample_rate=48000, channels=1, requested_profile="cpu", max_attempts=1,
                     max_text_chars=512, max_duration_error_ticks=80,
                     min_speed_ratio_milli=800, max_speed_ratio_milli=1300,
                     resource=ResourceProfile(max_threads=2, max_memory_mb=2048, max_batch_items=1))


def admit_production_cue(provenance, config: TtsConfig) -> None:
    """Pure producer/config admission; it does not verify installed model bytes."""
    if (provenance.producer, provenance.producer_version, provenance.backend_id, provenance.runtime,
        provenance.hardware_profile, provenance.fallback_reason) != (
        "dubflow-production-tts", B2_TTS_PRODUCER_VERSION, vieneu.ENGINE_ID, vieneu.RUNTIME_ID, "cpu", None):
        raise ReviewError("installed cue producer differs from pinned job; compatibility admission required")
    if config != production_cue_config():
        raise ReviewError("installed cue configuration differs from pinned job")


def load_production_cue_engine(request: CueSynthesisInput, *, app_root: Path,
                               model_root: Path, profile_path: Path, ffmpeg_path: Path):
    """Admit the current installed producer; older jobs need explicit compatibility."""
    admit_production_cue(request.provenance, request.config)
    for path in (app_root, model_root, profile_path, ffmpeg_path):
        plain(Path(path).absolute())
    if not Path(ffmpeg_path).is_file():
        raise ReviewError("owned cue FFmpeg is unavailable")
    pack, voice = vieneu.load_vieneu_voice(app_root, model_root, profile_path, voice_id=request.voice.voice_id)
    if voice != request.voice:
        raise ReviewError("installed voice/model profile differs from pinned request")
    return vieneu.VieNeuVietnameseTtsEngine(pack, ffmpeg_path=ffmpeg_path)


def cue_checkpoint_identity(request: CueSynthesisInput, recipe_identity: str) -> str:
    if not isinstance(recipe_identity, str) or not 1 <= len(recipe_identity) <= 256:
        raise ReviewError("cue synthesis recipe identity is invalid")
    return hashlib.sha256(json.dumps({"request": request.input_hash, "recipe": recipe_identity},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def synthesize_cue_candidate(raw: bytes, *, expected_hash: str, output_root: Path,
                             engine_factory: Callable[[CueSynthesisInput], object],
                             recipe_identity: str) -> CueCandidate:
    """Produce one private candidate, revalidating checkpoints on every resume.

    engine_factory must return the supervisor-admitted engine. The production
    entry point below never permits the caller to choose a fixture backend.
    Returning a candidate proves only TTS/spoken-text validation, not mix/render,
    QC, export publication or retirement of the previous valid artifact graph.
    """
    request = read_cue_synthesis_input(raw, expected_hash=expected_hash)
    identity = cue_checkpoint_identity(request, recipe_identity)
    generation = Path(output_root).absolute() / ("cue-" + request.input_hash.removeprefix("sha256:"))
    plain(generation)
    generation.mkdir(parents=True, exist_ok=True)
    with _writer(generation):
        input_path = generation / "input.json"
        plain(input_path)
        if input_path.exists():
            with input_path.open("rb") as stream:
                if stream.read(MAX_REQUEST_BYTES + 1) != raw:
                    raise ReviewError("cue candidate generation has another request")
        else:
            _atomic(input_path, raw)
        audio = generation / "tts"
        checkpoints = TtsCheckpointStore(audio, identity=identity)
        spoken = SpokenTextStore(audio, identity=identity, segments=(request.segment,))
        reusable = spoken.filter_checkpoints(checkpoints.load((request.segment,)))
        engine = BoundedDurationRewriteEngine(engine_factory(request), spoken)
        try:
            def commit(checkpoint):
                spoken.commit(checkpoint)
                checkpoints.commit(checkpoint)
            document = LocalTtsAdapter(engine, config=request.config, voice=request.voice,
                                      provenance=request.provenance, output_dir=audio).synthesize(
                                      (request.segment,), input_hash=request.input_hash,
                                      checkpoints=reusable, on_checkpoint=commit)
        finally:
            engine.close()
        if (document.source_segment_ids != (request.segment.segment_id,) or len(document.artifacts) != 1 or
            document.failures or document.provenance != request.provenance):
            raise ReviewError("cue candidate did not produce exactly one validated pinned artifact")
        document = replace(document, warnings=document.warnings + tuple(checkpoints.warnings) + tuple(spoken.warnings))
        # A verified artifact is complete on both inference and checkpoint reuse.
        # Execution observations must not change accepted document/composition
        # hashes and force another full-length mix for the same correction.
        observation = {"schema_version":1,"kind":"review_cue_execution_observation",
                       "request_sha256":request.input_hash,"recipe_identity":recipe_identity,
                       "chunks":[dict(chunk) for chunk in document.chunks],"warnings":list(document.warnings)}
        _atomic(generation / "execution_observation_v1.json",
                (json.dumps(observation,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode())
        reused_warning = "reused TTS checkpoint for " + request.segment.segment_id
        document = replace(document,
                chunks=tuple({**chunk,"status":"completed"} if chunk["status"] == "skipped" else dict(chunk) for chunk in document.chunks),
                warnings=tuple(warning for warning in document.warnings if warning != reused_warning))
        speech = spoken.document(document)
        validate_spoken_text_document(speech, document.to_bytes(), (request.segment,), identity=identity)
        speech_path, document_path = generation / "spoken_text.json", generation / "tts_document.json"
        # No graph pointer exists here. Both files must be independently pinned
        # and validated by the supervisor before a later graph transaction.
        _atomic(speech_path, (json.dumps(speech, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
        _atomic(document_path, document.to_bytes())
        return CueCandidate(document, document_path, speech_path)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("request", "app-root", "model-root", "profile", "ffmpeg", "output-root"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--request-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
            raise ReviewError("cue worker requires owned Python -I -S -B")
        plain(args.request.absolute())
        with args.request.open("rb") as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
        # Pin before evaluating a recipe or creating private candidate paths.
        request = read_cue_synthesis_input(raw, expected_hash=args.request_sha256)
        recipe = tts_recipe_identity(args.app_root, args.profile)
        def factory(admitted):
            return load_production_cue_engine(admitted, app_root=args.app_root,
                       model_root=args.model_root, profile_path=args.profile, ffmpeg_path=args.ffmpeg)
        result = synthesize_cue_candidate(raw, expected_hash=request.input_hash, output_root=args.output_root,
                                         engine_factory=factory, recipe_identity=recipe)
        sys.stdout.write(result.document.to_json() + "\n")
        return 0
    except Exception as error:
        condition = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code": "REVIEW_CUE_SYNTHESIS_FAILED", "message": condition}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

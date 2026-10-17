"""Owned bounded single-cue production and immutable audio metadata retargeting.

Only a native caller admits roots/requests/accepted audio. This process neither
opens SQLite nor publishes an active playback graph, mix, render or export.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

MAX_PACKET = 256 * 1024
MAX_DOCUMENT = 64 * 1024


def _imports():
    # Called only after the native containment gate by the executable entry.
    if __package__ in (None, ""):
        # The app's packaging/ namespace must not shadow runtime dependencies.
        sys.path.append(str(Path(sys.executable).resolve().parent / "Lib" / "site-packages"))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def candidate_packet(request, candidate, recipe_identity: str) -> dict:
    from engine.dubflow.review.synthesis_input import _digest
    from engine.dubflow.review.wire import decode_review_json
    from engine.dubflow.worker.tts_rewrites import validate_spoken_text_document
    from engine.dubflow.review.synthesis_worker import cue_checkpoint_identity
    from engine.dubflow.review.production import ReviewError
    tts = candidate.document.to_bytes()
    with candidate.spoken_text_path.open("rb") as stream:
        spoken = stream.read(MAX_DOCUMENT + 1)
    if len(tts) > MAX_DOCUMENT or len(spoken) > MAX_DOCUMENT:
        raise ReviewError("single-cue candidate metadata exceeds its bound")
    if candidate.document.provenance != request.provenance or candidate.document.failures:
        raise ReviewError("candidate producer differs from the admitted single cue")
    validate_spoken_text_document(decode_review_json(spoken.decode("utf-8")), tts,
        (request.segment,), identity=cue_checkpoint_identity(request, recipe_identity))
    return {"schema_version": 1, "kind": "review_cue_candidate", "request_sha256": request.input_hash,
        "recipe_identity": recipe_identity, "tts_document_json": tts.decode("utf-8"),
        "tts_document_sha256": _digest(tts), "spoken_text_json": spoken.decode("utf-8"),
        "spoken_text_sha256": _digest(spoken)}


def accepted_packet(raw: bytes) -> dict:
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.wire import decode_review_json
    from engine.dubflow.review.synthesis_input import _digest, read_cue_synthesis_input
    from engine.dubflow.review.accepted_documents import AcceptedWavePin, prepare_accepted_cue_documents
    if not 1 <= len(raw) <= MAX_PACKET:
        raise ReviewError("native accepted context exceeds its bound")
    context = decode_review_json(raw.decode("utf-8"))
    fields = {"schema_version", "kind", "request_json", "request_sha256", "candidate_tts_json",
              "candidate_spoken_text_json", "recipe_identity", "accepted_wave"}
    if (not isinstance(context, dict) or set(context) != fields or type(context["schema_version"]) is not int or
        context["schema_version"] != 1 or context["kind"] != "review_native_accepted_cue_context"):
        raise ReviewError("unknown native accepted cue context")
    for field in ("request_json", "candidate_tts_json", "candidate_spoken_text_json", "recipe_identity"):
        if not isinstance(context[field], str):
            raise ReviewError("accepted context requires exact producer JSON strings")
    pin = context["accepted_wave"]
    if not isinstance(pin, dict) or set(pin) != {"path", "root", "sha256", "size_bytes", "sample_rate", "channels", "frame_count"}:
        raise ReviewError("accepted context lacks the native PCM descriptor")
    request = read_cue_synthesis_input(context["request_json"].encode("utf-8"), expected_hash=context["request_sha256"])
    wave = AcceptedWavePin(**{**pin, "path":Path(pin["path"]), "root":Path(pin["root"])})
    documents = prepare_accepted_cue_documents(request, candidate_tts=context["candidate_tts_json"].encode("utf-8"),
        candidate_spoken_text=context["candidate_spoken_text_json"].encode("utf-8"), accepted_wave=wave,
        recipe_identity=context["recipe_identity"])
    if len(documents.tts) > MAX_DOCUMENT or len(documents.spoken_text) > MAX_DOCUMENT:
        raise ReviewError("accepted cue metadata exceeds its bound")
    return {"schema_version":1, "kind":"review_accepted_cue_documents", "request_sha256":request.input_hash,
        "tts_document_json":documents.tts.decode("utf-8"), "tts_document_sha256":_digest(documents.tts),
        "spoken_text_json":documents.spoken_text.decode("utf-8"), "spoken_text_sha256":_digest(documents.spoken_text)}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    produce = commands.add_parser("produce")
    for name in ("request", "app-root", "model-root", "profile", "ffmpeg", "output-root"):
        produce.add_argument("--" + name, type=Path, required=True)
    produce.add_argument("--request-sha256", required=True)
    accept = commands.add_parser("accept")
    accept.add_argument("--context", type=Path, required=True)
    accept.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("cue execution requires owned Python -I -S -B and native byte gate")
        _imports()
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode, read_cue_synthesis_input
        if args.action == "accept":
            result = accepted_packet(_json_bytes(args.context, args.context_sha256))
        else:
            from engine.dubflow.review.existing_models import admit_existing_cue_pack
            from engine.dubflow.review.synthesis_worker import synthesize_cue_candidate
            from engine.dubflow.tts.vieneu import VieNeuVietnameseTtsEngine
            from engine.dubflow.worker.b2_audio import tts_recipe_identity
            from engine.dubflow.worker.export_publication import plain
            raw = _json_bytes(args.request, args.request_sha256)
            request = read_cue_synthesis_input(raw, expected_hash=args.request_sha256)
            plain(args.ffmpeg.absolute())
            if not args.ffmpeg.is_file():
                raise ValueError("original owned cue media executable is unavailable")
            pack = admit_existing_cue_pack(request, app_root=args.app_root, model_root=args.model_root, profile_path=args.profile)
            recipe = tts_recipe_identity(args.app_root, args.profile)
            candidate = synthesize_cue_candidate(raw, expected_hash=request.input_hash, output_root=args.output_root,
                engine_factory=lambda admitted: VieNeuVietnameseTtsEngine(pack, ffmpeg_path=args.ffmpeg), recipe_identity=recipe)
            result = {**candidate_packet(request, candidate, recipe), "model_weights_verified": True}
        sys.stdout.buffer.write(_encode(result, MAX_PACKET - 1) + b"\n")
        return 0
    except Exception as error:
        condition = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code":"REVIEW_CUE_EXECUTION_FAILED", "message":condition}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

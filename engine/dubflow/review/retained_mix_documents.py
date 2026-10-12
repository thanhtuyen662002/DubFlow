"""Retarget admitted mix metadata to native retained audio, without media I/O.

Native code owns OS volume/locator/PCM admission and final revalidation. This
adapter changes three paths only; it grants no graph or storage authority and
does not run a producer, inspect mutable candidate files or write SQLite.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys

MAX_PACKET = 64 * 1024 * 1024
ROLES = ("original_audio", "dialogue_stem", "final_mix")
MAX_MIX_BYTES = 2**32 + 7
MAX_FRAMES = 48000 * 6 * 3600


def _imports():
    if __package__ in (None, ""):
        sys.path.append(str(Path(sys.executable).resolve().parent / "Lib/site-packages"))
        sys.path.append(str(Path(__file__).resolve().parents[3]))


def prepare_retained_mix_document(raw: bytes) -> dict:
    from engine.dubflow.mix.adapter import parse_mix_json
    from engine.dubflow.review.export import _hash
    from engine.dubflow.review.production import ReviewError
    from engine.dubflow.review.synthesis_input import _digest, _encode
    from engine.dubflow.review.wire import decode_review_json

    def refuse(message):
        raise ReviewError(message)

    def closed(value, fields):
        if not isinstance(value, dict) or set(value) != set(fields):
            refuse("retained mix context has missing or unknown fields")

    def wide(value, minimum, maximum):
        if (not isinstance(value, str) or len(value) > 20 or
                re.fullmatch(r"0|[1-9][0-9]*", value) is None):
            refuse("retained PCM counts require canonical decimal strings")
        result = int(value)
        if not minimum <= result <= maximum:
            refuse("retained PCM count exceeds its native bound")
        return result

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PACKET:
        refuse("retained mix context exceeds its byte bound")
    context = decode_review_json(raw.decode("utf-8"))
    closed(context, ("schema_version", "kind", "job_id", "output_root", "volume_id",
                     "candidate_mix_json", "candidate_mix_sha256", "composition_sha256", "retained_mix_audio"))
    if (type(context["schema_version"]) is not int or context["schema_version"] != 1 or
            context["kind"] != "review_native_retained_mix_context"):
        refuse("unknown retained mix version requires explicit compatibility")
    job, volume = context["job_id"], context["volume_id"]
    if (not isinstance(job, str) or not 1 <= len(job) <= 256 or
            not isinstance(volume, str) or not 1 <= len(volume) <= 256 or
            any(ord(c) < 32 or ord(c) == 127 for c in job + volume)):
        refuse("retained mix needs native job and stable volume pins")
    root_raw = context["output_root"]
    if (not isinstance(root_raw, str) or not root_raw or len(root_raw) > 32767 or
            any(ord(c) < 32 or ord(c) == 127 for c in root_raw)):
        refuse("retained mix lacks a bounded output root hint")
    root = Path(root_raw)
    if not root.is_absolute() or ".." in root.parts:
        refuse("retained mix root must be an absolute native hint")
    candidate = context["candidate_mix_json"]
    if (not isinstance(candidate, str) or not candidate or
            len(candidate.encode("utf-8")) > MAX_PACKET):
        refuse("retained mix requires bounded exact candidate bytes")
    _hash(context["candidate_mix_sha256"]); _hash(context["composition_sha256"])
    if _digest(candidate.encode("utf-8")) != context["candidate_mix_sha256"]:
        refuse("retained mix candidate checksum changed")
    mix = parse_mix_json(candidate)
    if (mix["provenance"]["input_hash"] != context["composition_sha256"] or
            mix["sample_rate"] != 48000 or mix["channels"] != 2):
        refuse("retained mix differs from its native composition or PCM layout")
    assignments = context["retained_mix_audio"]
    if not isinstance(assignments, list) or len(assignments) != 3:
        refuse("retained mix needs exactly three native role pins")
    job_namespace = hashlib.sha256(job.encode("utf-8")).hexdigest()
    for role, assignment in zip(ROLES, assignments):
        closed(assignment, ("role", "audio"))
        if assignment["role"] != role:
            refuse("retained mix roles are missing, repeated or out of order")
        audio = assignment["audio"]
        closed(audio, ("path_hint", "locator", "sample_rate", "channels", "frame_count"))
        locator = audio["locator"]
        closed(locator, ("schema_version", "volume_id", "root_kind", "relative_path", "content_hash", "size_bytes"))
        if (type(locator["schema_version"]) is not int or locator["schema_version"] != 1 or
                locator["volume_id"] != volume or locator["root_kind"] != "output"):
            refuse("retained locator changes native volume, root kind or version")
        _hash(locator["content_hash"])
        relative = Path(".dubflow-review-media-v1") / job_namespace / (locator["content_hash"][7:] + ".wav")
        if (locator["relative_path"] != str(relative) or
                audio["path_hint"] != str(root / relative)):
            refuse("retained audio path/locator differs from the native job namespace")
        size = wide(locator["size_bytes"], 44, MAX_MIX_BYTES)
        frames = wide(audio["frame_count"], 1, MAX_FRAMES)
        artifact = mix[role]
        if (type(audio["sample_rate"]) is not int or audio["sample_rate"] != 48000 or
                type(audio["channels"]) is not int or audio["channels"] != 2 or
                size < 44 + 4 * frames or frames != mix["original_audio"]["frame_count"] or
                artifact["content_hash"] != locator["content_hash"] or
                artifact["frame_count"] != frames or artifact["sample_rate"] != audio["sample_rate"] or
                artifact["channels"] != audio["channels"]):
            refuse("retained role substitutes native content, full-mix frames or PCM layout")
        # No producer/source/hash/metrics/timing/chunk/confidence/quality edit.
        artifact["path"] = audio["path_hint"]
    prepared = _encode(mix, MAX_PACKET) + b"\n"
    parse_mix_json(prepared)
    return {"schema_version": 1, "kind": "review_retained_mix_document", "context_sha256": _digest(raw),
            "composition_sha256": context["composition_sha256"],
            "mix_document_json": prepared.decode("utf-8"), "mix_document_sha256": _digest(prepared),
            "native_media_revalidation_required": True, "active_graph_published": False}


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or
                sys.stdin.buffer.read(1) != b"\x01"):
            raise ValueError("retained metadata requires owned Python -I -S -B and native byte gate")
        _imports()
        from engine.dubflow.review.export import _json_bytes
        from engine.dubflow.review.synthesis_input import _encode
        packet = prepare_retained_mix_document(_json_bytes(args.context, args.context_sha256))
        sys.stdout.buffer.write(_encode(packet, MAX_PACKET)); sys.stdout.buffer.flush()
        return 0
    except Exception as error:
        sys.stderr.write(json.dumps({"code": "REVIEW_MIX_RETARGET_FAILED", "message": str(error)[:2048]}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

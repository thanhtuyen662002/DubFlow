"""Owned read-only cue request preparation after native runtime admission.

Native archives and the immutable job producer binding supply all authority.
Catalog metadata is pinned by that native boundary. No provisioning, inference,
media admission, graph publication or SQL occurs in this adapter.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engine.dubflow.models.runtime import ModelArtifact
from engine.dubflow.tts import ResourceProfile, TtsInput, TtsProvenance, VoiceProfile
from engine.dubflow.tts import vieneu
from engine.dubflow.tts.adapter import _parse_point
from engine.dubflow.tts.neural_vits import _child, _json_bytes as _catalog_bytes
from engine.dubflow.tts.vieneu_native import FRONTEND_ID, INFERENCE_RECIPE, VERSIONS
from engine.dubflow.review.export import MAX_JSON_BYTES, _json_bytes
from engine.dubflow.review.production import ReviewError, ReviewSnapshot
from engine.dubflow.review.synthesis_input import _digest, _encode, prepare_cue_synthesis
from engine.dubflow.review.synthesis_worker import admit_production_cue, production_cue_config
from engine.dubflow.review.wire import decode_review_json, read_review_snapshot


def _read_metadata(path: Path) -> tuple[bytes, dict]:
    with path.open("rb") as stream:
        raw = stream.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ReviewError("pinned catalog metadata exceeds its byte limit")
    value = decode_review_json(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ReviewError("pinned catalog metadata needs an object")
    return raw, value


def catalog_voice(app_root: Path, profile_path: Path, voice_id: str) -> VoiceProfile:
    """Reconstruct the installed preset profile without reading/downloading weights.

    The subsequent production engine loader must verify the actual model tree.
    Merely returning this profile cannot authorize inference or media reuse.
    """
    app_root = Path(app_root)
    if not app_root.is_absolute() or ".." in app_root.parts or not profile_path.is_relative_to(app_root):
        raise ReviewError("catalog metadata is outside its native app root")
    _, selection = _read_metadata(profile_path)
    metadata = _child(app_root, selection.get("tts_neural_profile", ""))
    raw, profile = _read_metadata(metadata)
    if (type(profile.get("schema_version")) is not int or profile["schema_version"] != 1 or
        profile.get("backend") != vieneu.ENGINE_ID or profile.get("frontend") != FRONTEND_ID or
        profile.get("runtime_versions") != VERSIONS or profile.get("sample_rate") != 48000 or
        profile.get("inference") != INFERENCE_RECIPE or profile.get("license_id") != "Apache-2.0" or
        profile.get("approved") is not True):
        raise ReviewError("catalog recipe/runtime/license needs explicit compatibility admission")
    choices = vieneu.voice_choices(profile)
    selected = next((item for item in choices if item["voice_id"] == voice_id), None)
    if selected is None:
        raise ReviewError("selected voice is absent from the job's pinned catalog")
    tree = profile.get("model_tree_sha256")
    if not isinstance(tree, str) or len(tree) != 64 or any(c not in "0123456789abcdef" for c in tree):
        raise ReviewError("catalog model tree hash is invalid")
    inventory = profile.get("artifacts")
    if not isinstance(inventory, list) or len(inventory) != len(vieneu.FILES):
        raise ReviewError("catalog model inventory is incomplete")
    prefix = "tts/packs/vieneu-" + tree + "/"
    records = {}
    for item in inventory:
        artifact = ModelArtifact.from_mapping(item)
        if not artifact.relative_path.startswith(prefix):
            raise ReviewError("catalog model inventory escapes its pinned pack")
        relative = artifact.relative_path[len(prefix):]
        if relative not in vieneu.FILES or relative in records or artifact.size_bytes <= 0:
            raise ReviewError("catalog model inventory is ambiguous")
        records[relative] = {"sha256": artifact.sha256, "size_bytes": artifact.size_bytes}
    if set(records) != vieneu.FILES or hashlib.sha256(_catalog_bytes(records)).hexdigest() != tree:
        raise ReviewError("catalog model inventory hash differs")
    return VoiceProfile(voice_id=voice_id, voice_version=selected["voice_version"], language="vi",
        display_name=selected["name"], model_id=profile["model_id"], model_version=profile["model_version"],
        model_hash="sha256:" + tree, manifest_hash=_digest(raw), license_id="Apache-2.0", approved=True,
        network_required=False, credential_required=False, default=voice_id == profile["voice_id"])


def prepare_original_cue(context_raw: bytes, *, app_root: Path, profile_path: Path) -> dict:
    if not isinstance(context_raw, bytes) or not 1 <= len(context_raw) <= MAX_JSON_BYTES:
        raise ReviewError("native cue context exceeds its byte limit")
    context = decode_review_json(context_raw.decode("utf-8"))
    fields = {"schema_version", "kind", "manifest_sha256", "before_snapshot_json", "after_snapshot_json", "source_graph_json"}
    if (not isinstance(context, dict) or set(context) != fields or type(context["schema_version"]) is not int or
        context["schema_version"] != 1 or context["kind"] != "review_original_cue_context"):
        raise ReviewError("unknown native cue context needs explicit compatibility")
    if any(not isinstance(context[key], str) for key in ("before_snapshot_json", "after_snapshot_json", "source_graph_json")):
        raise ReviewError("cue context requires exact source JSON strings")
    before = read_review_snapshot(decode_review_json(context["before_snapshot_json"]))
    after = read_review_snapshot(decode_review_json(context["after_snapshot_json"]))
    graph = decode_review_json(context["source_graph_json"])
    if (not isinstance(before, ReviewSnapshot) or not isinstance(after, ReviewSnapshot) or
        not isinstance(graph, dict) or graph.get("schema_version") != 1 or graph.get("kind") != "review_original_graph" or
        graph.get("revision") != "0" or graph.get("mode") != "b2" or before.revision != 0 or after.last_plan is None or
        graph.get("job_id") != before.job_id or graph.get("manifest_sha256") != context["manifest_sha256"] or
        graph.get("time_base") != before.to_dict()["time_base"]):
        raise ReviewError("cue context is not the native original B2 generation")
    selected = next((f for f in after.findings if f.finding_id == after.last_plan.finding_id), None)
    inputs = graph.get("canonical_inputs")
    if selected is None or selected.cue_id is None or not isinstance(inputs, list) or not 1 <= len(inputs) <= 10000:
        raise ReviewError("cue context lacks a bounded original source assignment")
    by_id = {}
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("segment_id"), str) or item["segment_id"] in by_id:
            raise ReviewError("cue context has ambiguous original inputs")
        by_id[item["segment_id"]] = item
    item = by_id.get(selected.cue_id)
    if item is None:
        raise ReviewError("selected finding has no original canonical input")
    original = TtsInput(item["segment_id"], item["source_utterance_id"], item["text"],
        _parse_point(item["start"], "start"), _parse_point(item["end"], "end"), source_language=item["source_language"],
        target_language=item["target_language"], confidence=item["confidence"],
        render_window_end=_parse_point(item["render_window_end"], "render_window_end") if "render_window_end" in item else None)
    if original.to_dict() != item:
        raise ReviewError("original input differs from its normalized canonical representation")
    values = dict(graph["tts_provenance"]); values["resource"] = ResourceProfile(**values["resource"])
    previous = TtsProvenance(**values)
    config = production_cue_config()
    admit_production_cue(previous, config)
    if previous.config_hash != config.content_hash() or previous.resource != config.resource:
        raise ReviewError("original producer configuration needs explicit compatibility")
    voice = catalog_voice(app_root, profile_path, selected.voice)
    target = replace(previous, voice_id=voice.voice_id, voice_version=voice.voice_version, voice_hash=voice.content_hash())
    request = prepare_cue_synthesis(before, after, manifest_sha256=context["manifest_sha256"], original_input=original,
        previous_provenance=previous, target_provenance=target, voice=voice, config=config)
    return {"schema_version":1,"kind":"review_prepared_cue_request","context_sha256":_digest(context_raw),
            "request_json":request.encoded.decode("utf-8"),"request_sha256":request.input_hash,
            "config":config.to_dict(),"voice":voice.to_dict(),"model_weights_verified":False}


def main(argv=None):
    import argparse, json
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "app-root", "profile"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode or sys.stdin.buffer.read(1) != b"\x01":
            raise ReviewError("cue planning requires owned Python -I -S -B and native byte gate")
        raw = _json_bytes(args.context, args.context_sha256)
        result = prepare_original_cue(raw, app_root=args.app_root, profile_path=args.profile)
        sys.stdout.buffer.write(_encode(result, 64 * 1024) + b"\n")
        return 0
    except Exception as error:
        condition = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code":"REVIEW_CUE_PLAN_INVALID","message":condition},ensure_ascii=True)+"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

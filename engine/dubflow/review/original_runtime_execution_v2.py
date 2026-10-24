"""Isolated split-v2 metadata dispatcher.

Native owns both complete release admissions, current-state fences and the child
lifetime. This entry validates the sealed role/context/recipe binding and consumes
one containment gate. It performs no model inference or graph publication.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

LIMIT = 64 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
DOMAIN = b"review-original-runtime-processing-app-v2\0"
ACTIONS = ("prepare_effective_cue", "prepare_effective_timeline")
ROLE_FIELDS = frozenset(("original_runtime_root", "original_python",
    "processing_app_root", "original_model_root", "original_media_root",
    "ffmpeg", "ffprobe", "profile"))
BINDING_FIELDS = frozenset(("schema_version", "kind", "action", "current_token",
    "context_pin", "processing_authority_receipt_pin", "original_producer_sha256",
    "roles", "recipe_dependencies", "execution_recipe_sha256"))
AUTHORITY_FIELDS = frozenset(("schema_version", "kind", "route", "capability_document",
    "source_sha", "processing_root", "release_manifest", "original_producer_sha256",
    "original_release_sha256", "original_runtime_root", "original_python_pin",
    "processing_app_root", "processing_app_inventory_sha256", "original_model_root",
    "original_profile_pin", "original_catalog_sha256", "roles",
    "original_protected_inventory_sha256", "resolved_protected_inventory_sha256",
    "original_helper_inventory_sha256", "launch_policy_sha256",
    "original_job_binding_changed", "model_weights_verified", "active_graph_published"))
COMMON_DEPENDENCIES = (
    ("original_runtime", "runtime/python.exe"),
    ("processing_app", "engine/dubflow/review/original_runtime_execution_v2.py"),
)
ACTION_DEPENDENCIES = {
    "prepare_effective_cue": (
        ("processing_app", "engine/dubflow/review/effective_cue_execution.py"),
        ("processing_app", "engine/dubflow/review/effective_cue_plan.py"),
    ),
    "prepare_effective_timeline": (
        ("processing_app", "engine/dubflow/review/effective_timeline_execution.py"),
        ("processing_app", "engine/dubflow/review/corrected_timeline.py"),
    ),
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def closed(value, fields):
    need(type(value) is dict and set(value) == set(fields), "unknown split-v2 fields")


def _pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "duplicate split-v2 key")
        result[key] = value
    return result


def decode(raw, limit):
    need(type(raw) is bytes and 0 < len(raw) <= limit, "split-v2 input byte bound")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite split-v2 JSON")))


def canonical(value):
    # Recipe documents contain only bounded integers, strings, bools and null.
    # Inner native cue/snapshot JSON remains an exact string, never re-encoded.
    def checked(item):
        if type(item) is dict:
            need(all(type(k) is str for k in item), "string recipe keys required")
            for v in item.values():
                checked(v)
        elif type(item) is list:
            for v in item:
                checked(v)
        else:
            need(item is None or type(item) in (str, bool, int), "noninteger recipe number")
            if type(item) is int:
                need(0 <= item <= (1 << 63) - 1, "recipe integer bound")
    checked(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def valid_hash(value):
    return type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def pin(value, limit=8 * 1024 * 1024 * 1024):
    closed(value, ("sha256", "size_bytes"))
    need(valid_hash(value["sha256"]) and type(value["size_bytes"]) is int
        and 0 <= value["size_bytes"] <= limit, "invalid split-v2 pin")


def descriptor(value, limit=LIMIT):
    closed(value, ("path", "pin"))
    need(type(value["path"]) is str and 0 < len(value["path"]) <= 32767,
        "bounded split-v2 path required")
    pin(value["pin"], limit)


def current_token(value):
    closed(value, ("job_id", "revision", "snapshot_sha256", "graph_sha256",
        "original_manifest_sha256"))
    need(type(value["job_id"]) is str and 0 < len(value["job_id"]) <= 256
        and type(value["revision"]) is str
        and re.fullmatch(r"0|[1-9][0-9]{0,18}", value["revision"]) is not None
        and int(value["revision"]) <= (1 << 63) - 1, "invalid split-v2 current token")
    need(all(valid_hash(value[k]) for k in
        ("snapshot_sha256", "graph_sha256", "original_manifest_sha256")),
        "invalid split-v2 current hashes")


def plain_path(text, *, directory=False):
    need(type(text) is str and 0 < len(text) <= 32767, "bounded native role path")
    path = Path(text)
    need(path.is_absolute() and ".." not in path.parts, "absolute native role path")
    for ancestor in reversed(path.parents):
        s = ancestor.lstat()
        need(stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode)
            and not getattr(s, "st_file_attributes", 0) & 0x400,
            "linked native role ancestor")
    s = path.lstat()
    need(not stat.S_ISLNK(s.st_mode) and not getattr(s, "st_file_attributes", 0) & 0x400,
        "linked native role path")
    need(stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode),
        "native role type differs")
    return path


def _stable(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink,
        getattr(s, "st_file_attributes", 0))


def read_pinned(path, expected, limit, *, capture=True):
    pin(expected, limit)
    need(0 < expected["size_bytes"] <= limit, "empty native input document")
    path = plain_path(str(path))
    before = path.lstat()
    need(before.st_nlink == 1 and before.st_size == expected["size_bytes"],
        "native input size/link differs")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        need(_stable(opened) == _stable(before), "native input replaced before read")
        count, chunks, hasher = 0, [], hashlib.sha256()
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            count += len(chunk)
            need(count <= expected["size_bytes"], "native input grew during read")
            hasher.update(chunk)
            if capture:
                chunks.append(chunk)
        need(_stable(os.fstat(stream.fileno())) == _stable(opened),
            "native input changed during read")
    need(_stable(path.lstat()) == _stable(before)
        and count == expected["size_bytes"]
        and "sha256:" + hasher.hexdigest() == expected["sha256"],
        "native input full hash/identity differs")
    return b"".join(chunks) if capture else None


def execution_recipe(binding):
    value = {k: v for k, v in binding.items() if k != "execution_recipe_sha256"}
    return digest(DOMAIN + canonical(value))


def validate_launch(binding_raw, authority_raw, context_raw):
    """Pure closed protocol checks; native full physical admission remains required."""
    binding, authority = decode(binding_raw, MANIFEST_LIMIT), decode(authority_raw, MANIFEST_LIMIT)
    closed(binding, BINDING_FIELDS)
    closed(authority, AUTHORITY_FIELDS)
    need(type(binding["schema_version"]) is int and binding["schema_version"] == 2
        and binding["kind"] == "review_native_split_launch_binding_v2"
        and binding["action"] in ACTIONS, "unsupported split-v2 launch/action")
    need(type(authority["schema_version"]) is int and authority["schema_version"] == 2
        and authority["kind"] == "review_admitted_processing_release_v2"
        and authority["route"] == "original_python_processing_app_v2",
        "unsupported split-v2 authority")
    for k in ("original_job_binding_changed", "model_weights_verified", "active_graph_published"):
        need(authority[k] is False, "metadata authority cannot claim execution/publication")
    current_token(binding["current_token"])
    closed(binding["roles"], ROLE_FIELDS)
    need(binding["roles"] == authority["roles"], "role binding differs from admitted authority")
    for k in ("original_python", "ffmpeg", "ffprobe", "profile"):
        descriptor(binding["roles"][k], 512 * 1024 * 1024)
    for k in ("original_runtime_root", "processing_app_root", "original_model_root", "original_media_root"):
        need(type(binding["roles"][k]) is str and 0 < len(binding["roles"][k]) <= 32767,
            "invalid split-v2 role root")
    descriptor(binding["context_pin"])
    pin(binding["processing_authority_receipt_pin"], MANIFEST_LIMIT)
    need(binding["processing_authority_receipt_pin"] ==
        {"sha256": digest(authority_raw), "size_bytes": len(authority_raw)},
        "authority exact bytes differ")
    need(binding["context_pin"]["pin"] ==
        {"sha256": digest(context_raw), "size_bytes": len(context_raw)},
        "context exact bytes differ")
    need(valid_hash(binding["original_producer_sha256"])
        and binding["original_producer_sha256"] == authority["original_producer_sha256"],
        "original producer substituted")
    need(valid_hash(authority["original_protected_inventory_sha256"])
        and authority["original_protected_inventory_sha256"] ==
            authority["resolved_protected_inventory_sha256"],
        "complete resolved seven-prefix inventory differs")
    for k in ("original_release_sha256", "processing_app_inventory_sha256",
              "original_catalog_sha256", "original_helper_inventory_sha256", "launch_policy_sha256"):
        need(valid_hash(authority[k]), "invalid admitted authority identity")
    need(type(authority["source_sha"]) is str and
        re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", authority["source_sha"]) is not None,
        "processing source must be exact")
    pin(authority["release_manifest"], MANIFEST_LIMIT)
    pin(authority["capability_document"], 65536)
    roles = binding["roles"]
    need(authority["original_runtime_root"] == roles["original_runtime_root"]
        and authority["processing_app_root"] == roles["processing_app_root"]
        and authority["original_model_root"] == roles["original_model_root"]
        and authority["original_python_pin"] == roles["original_python"]["pin"]
        and authority["original_profile_pin"] == roles["profile"]["pin"],
        "authority duplicate roles differ")
    rows = binding["recipe_dependencies"]
    expected = COMMON_DEPENDENCIES + ACTION_DEPENDENCIES[binding["action"]]
    need(type(rows) is list and len(rows) == len(expected), "incomplete metadata dependency closure")
    for row, (role, name) in zip(rows, expected):
        closed(row, ("role", "relative_path", "pin"))
        need(row["role"] == role and row["relative_path"] == name,
            "dependency role/order differs")
        pin(row["pin"])
    need(valid_hash(binding["execution_recipe_sha256"])
        and execution_recipe(binding) == binding["execution_recipe_sha256"],
        "native/Python split execution recipe differs")
    context = decode(context_raw, LIMIT)
    closed(context, ("schema_version", "kind", "action", "current_token", "context_json", "bindings_json"))
    need(type(context["schema_version"]) is int and context["schema_version"] == 2
        and context["kind"] == "review_split_action_context_v2"
        and context["action"] == binding["action"]
        and context["current_token"] == binding["current_token"],
        "action/context/current token differs")
    need(type(context["context_json"]) is str and 0 < len(context["context_json"].encode()) <= LIMIT,
        "bounded exact inner context required")
    if binding["action"] == "prepare_effective_cue":
        need(type(context["bindings_json"]) is str
            and 0 < len(context["bindings_json"].encode()) <= LIMIT,
            "complete exact current audio bindings required")
        audio_bindings = decode(context["bindings_json"].encode("utf-8"), LIMIT)
        need(type(audio_bindings) is dict
            and audio_bindings.get("current_token") == binding["current_token"],
            "audio bindings substitute the current token")
    else:
        need(context["bindings_json"] is None, "timeline cannot introduce audio bindings")
    inner = decode(context["context_json"].encode("utf-8"), LIMIT)
    token_key = "before_token" if binding["action"] == "prepare_effective_cue" else "current_token"
    need(type(inner) is dict and inner.get(token_key) == binding["current_token"],
        "inner metadata substitutes the current token")
    return binding, authority, context


def bootstrap(binding, authority):
    """Establish actual O runtime plus exactly one R/app, before engine imports."""
    roles = binding["roles"]
    original_root = plain_path(roles["original_runtime_root"], directory=True).resolve()
    runtime = plain_path(str(original_root / "runtime"), directory=True).resolve()
    app = plain_path(roles["processing_app_root"], directory=True).resolve()
    code_root = plain_path(authority["processing_root"], directory=True).resolve()
    need(app == code_root / "app" and app != runtime
        and Path(__file__).resolve() == app / "engine/dubflow/review/original_runtime_execution_v2.py",
        "dispatcher differs from the admitted processing app")
    actual_python = plain_path(sys.executable).resolve()
    selected_python = plain_path(roles["original_python"]["path"]).resolve()
    need(actual_python == selected_python == runtime / "python.exe"
        and Path(sys.base_prefix).resolve() == runtime
        and Path(sys.prefix).resolve() == runtime, "actual original Python authority differs")
    need(plain_path(roles["original_media_root"], directory=True).resolve() == runtime,
        "original media root differs from the original runtime")
    need(not any(k == "engine" or k.startswith("engine.") for k in sys.modules),
        "engine imported before split admission")
    for name, module in tuple(sys.modules.items()):
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        if name == "__main__" or origin in (None, "built-in", "frozen"):
            continue
        need(type(origin) is str and Path(origin).is_absolute()
            and Path(origin).resolve().is_relative_to(runtime),
            "preloaded module outside the original runtime")
    need(0 < len(sys.path) <= 16 and len(set(sys.path)) == len(sys.path),
        "unexpected isolated runtime search path")
    for entry in sys.path:
        need(type(entry) is str and entry and Path(entry).is_absolute()
            and Path(entry).resolve().is_relative_to(runtime),
            "ambient or processing runtime import authority")
    site = plain_path(str(runtime / "Lib/site-packages"), directory=True)
    need(site.resolve().is_relative_to(runtime), "original site-packages escaped")
    # -S plus direct append deliberately does not execute .pth files or sitecustomize.
    sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != site.resolve()]
    sys.path.extend((str(site), str(app)))
    for row in binding["recipe_dependencies"]:
        root = original_root if row["role"] == "original_runtime" else app
        read_pinned(root / row["relative_path"], row["pin"], 512 * 1024 * 1024, capture=False)
    for name in ("original_python", "ffmpeg", "ffprobe", "profile"):
        item = roles[name]
        selected = plain_path(item["path"]).resolve()
        parent = app if name == "profile" else runtime
        need(selected.is_relative_to(parent), "executable/profile escaped its role")
        read_pinned(selected, item["pin"], 512 * 1024 * 1024, capture=False)
    declaration_path = app / "engine/dubflow/review/processing-original-runtime-v2.json"
    declaration = decode(read_pinned(declaration_path, authority["capability_document"], 65536), 65536)
    need(canonical(declaration) == canonical({
        "schema_version": 2, "kind": "review_processing_original_runtime_code_capability",
        "route": "original_python_processing_app_v2", "capabilities": list(ACTIONS),
        "dispatcher": "engine/dubflow/review/original_runtime_execution_v2.py",
        "launch_binding_format": "review_native_split_launch_binding_v2",
        "result_format": "review_split_processing_result_v2",
        "protected_policy": "complete_resolved_seven_prefix_equality_v2",
        "original_helper_policy": "exact_original_tts_recipe_helpers_v2",
        "model_provisioning": False, "original_job_binding_changed": False,
        "active_graph_publication": False}), "unknown split capability declaration")


def dispatch(binding, context):
    raw = context["context_json"].encode("utf-8")
    if binding["action"] == "prepare_effective_cue":
        from engine.dubflow.review.effective_cue_execution import prepare_bound_cue
        packet = prepare_bound_cue(raw, context["bindings_json"].encode("utf-8"),
            app_root=Path(binding["roles"]["processing_app_root"]),
            profile_path=Path(binding["roles"]["profile"]["path"]))
        need(packet["before_token"] == binding["current_token"], "prepared cue token differs")
    elif binding["action"] == "prepare_effective_timeline":
        from engine.dubflow.review.effective_timeline_execution import prepare_timeline
        packet = prepare_timeline(raw)
        need(packet["current_token"] == binding["current_token"], "prepared timeline token differs")
    else:
        raise ValueError("unported split action")
    need(packet["context_sha256"] == digest(raw)
        and packet["model_weights_verified"] is False
        and packet["active_graph_published"] is False, "invalid metadata kernel claims")
    if binding["action"] == "prepare_effective_timeline":
        need(packet["correction_ready"] is False, "timeline is not a published correction")
    return packet


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binding", "authority"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
        parser.add_argument("--" + name + "-size-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
            "split execution requires original Python -I -S -B")
        need(sys.stdin.buffer.read(1) == b"\x01", "native containment gate required once")
        binding_raw = read_pinned(args.binding, {"sha256": args.binding_sha256,
            "size_bytes": args.binding_size_bytes}, MANIFEST_LIMIT)
        authority_raw = read_pinned(args.authority, {"sha256": args.authority_sha256,
            "size_bytes": args.authority_size_bytes}, MANIFEST_LIMIT)
        initial = decode(binding_raw, MANIFEST_LIMIT)
        closed(initial, BINDING_FIELDS)
        descriptor(initial["context_pin"])
        context_raw = read_pinned(initial["context_pin"]["path"], initial["context_pin"]["pin"], LIMIT)
        binding, authority, context = validate_launch(binding_raw, authority_raw, context_raw)
        bootstrap(binding, authority)
        result = dispatch(binding, context)
        envelope = {"schema_version": 2, "kind": "review_split_processing_result_v2",
            "action": binding["action"], "context_sha256": digest(context_raw),
            "authority_receipt_sha256": digest(authority_raw),
            "execution_recipe_sha256": binding["execution_recipe_sha256"], "result": result}
        # The outer envelope and newline share the original bounded stdout budget.
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"),
            sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        need(len(encoded) <= LIMIT, "split result including envelope exceeds byte bound")
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception as error:
        condition = str(error).encode("utf-8")[:1024].decode("utf-8", errors="ignore")
        sys.stderr.write(json.dumps({"code": "REVIEW_SPLIT_METADATA_INVALID",
            "message": condition}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

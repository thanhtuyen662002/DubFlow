"""Pure split-v2 wire regressions; these do not admit an installed release."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("split_metadata_v2",
    ROOT / "engine/dubflow/review/original_runtime_execution_v2.py")
SPLIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SPLIT)


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def h(char):
    return "sha256:" + char * 64


def pin(char="a", size=1):
    return {"sha256": h(char), "size_bytes": size}


def descriptor(path, char="a"):
    return {"path": path, "pin": pin(char)}


def fixture(action="prepare_effective_cue"):
    token = {"job_id": "original-job", "revision": "2", "snapshot_sha256": h("c"),
        "graph_sha256": h("d"), "original_manifest_sha256": h("e")}
    roles = {"original_runtime_root": "D:/original",
        "original_python": descriptor("D:/original/runtime/python.exe"),
        "processing_app_root": "D:/processing/app",
        "original_model_root": "D:/original-models",
        "original_media_root": "D:/original/runtime",
        "ffmpeg": descriptor("D:/original/runtime/media/ffmpeg.exe"),
        "ffprobe": descriptor("D:/original/runtime/media/ffprobe.exe"),
        "profile": descriptor("D:/processing/app/models/manifests/production-cpu-v1.json")}
    authority = {"schema_version": 2, "kind": "review_admitted_processing_release_v2",
        "route": "original_python_processing_app_v2", "capability_document": pin(),
        "source_sha": "f" * 40, "processing_root": "D:/processing",
        "release_manifest": pin(), "original_producer_sha256": h("b"),
        "original_release_sha256": h("c"), "original_runtime_root": "D:/original",
        "original_python_pin": roles["original_python"]["pin"],
        "processing_app_root": "D:/processing/app", "processing_app_inventory_sha256": h("d"),
        "original_model_root": "D:/original-models", "original_profile_pin": roles["profile"]["pin"],
        "original_catalog_sha256": h("e"), "roles": copy.deepcopy(roles),
        "original_protected_inventory_sha256": h("a"), "resolved_protected_inventory_sha256": h("a"),
        "original_helper_inventory_sha256": h("b"), "launch_policy_sha256": h("c"),
        "original_job_binding_changed": False, "model_weights_verified": False,
        "active_graph_published": False}
    # Deliberately retain spacing and Unicode inside the independently bound strings.
    inner = '{ "before_token": ' + json.dumps(token) + ', "text": "Một tâm hồn đồng điệu." }'
    if action == "prepare_effective_timeline":
        inner = inner.replace('"before_token"', '"current_token"')
    wrapper = {"schema_version": 2, "kind": "review_split_action_context_v2", "action": action,
        "current_token": copy.deepcopy(token), "context_json": inner,
        "bindings_json": json.dumps({"current_token": token}) if action == "prepare_effective_cue" else None}
    context_bytes = raw(wrapper)
    authority_bytes = raw(authority)
    dependencies = SPLIT.COMMON_DEPENDENCIES + SPLIT.ACTION_DEPENDENCIES[action]
    binding = {"schema_version": 2, "kind": "review_native_split_launch_binding_v2", "action": action,
        "current_token": token,
        "context_pin": {"path": "D:/private/context.json",
            "pin": {"sha256": SPLIT.digest(context_bytes), "size_bytes": len(context_bytes)}},
        "processing_authority_receipt_pin": {
            "sha256": SPLIT.digest(authority_bytes), "size_bytes": len(authority_bytes)},
        "original_producer_sha256": h("b"), "roles": roles,
        "recipe_dependencies": [{"role": role, "relative_path": name, "pin": pin()}
            for role, name in dependencies]}
    binding["execution_recipe_sha256"] = SPLIT.execution_recipe(binding)
    return binding, authority, wrapper


def seal(binding, authority, wrapper):
    context_bytes, authority_bytes = raw(wrapper), raw(authority)
    binding["context_pin"]["pin"] = {"sha256": SPLIT.digest(context_bytes), "size_bytes": len(context_bytes)}
    binding["processing_authority_receipt_pin"] = {
        "sha256": SPLIT.digest(authority_bytes), "size_bytes": len(authority_bytes)}
    binding["execution_recipe_sha256"] = SPLIT.execution_recipe(binding)
    return raw(binding), authority_bytes, context_bytes


class SplitWireTests(unittest.TestCase):
    def test_two_metadata_actions_preserve_both_context_hash_domains_and_exact_inner_strings(self):
        for action in SPLIT.ACTIONS:
            with self.subTest(action=action):
                b, a, w = fixture(action)
                original_inner = w["context_json"]
                returned = SPLIT.validate_launch(*seal(b, a, w))
                self.assertEqual(returned[2]["context_json"], original_inner)
                self.assertNotEqual(SPLIT.digest(original_inner.encode()), b["context_pin"]["pin"]["sha256"])
                self.assertEqual(returned[0]["action"], action)
        self.assertEqual(SPLIT.ACTIONS, ("prepare_effective_cue", "prepare_effective_timeline"))

    def test_unknown_versions_fields_duplicates_nonfinite_and_float_recipe_numbers_refuse(self):
        for target, key, value in [
            (0, "schema_version", 1), (0, "schema_version", True),
            (1, "schema_version", 1), (1, "schema_version", 2.0),
            (2, "schema_version", True), (0, "ignore_runtime", True),
            (1, "unknown", True), (2, "unknown", True)]:
            b, a, w = fixture()
            [b, a, w][target][key] = value
            with self.subTest(target=target, key=key, value=value), self.assertRaises(ValueError):
                SPLIT.validate_launch(*seal(b, a, w))
        for data in (b'{"a":1,"a":2}', b'{"value":NaN}', b'{"value":Infinity}'):
            with self.assertRaises(ValueError):
                SPLIT.decode(data, 256)
        for value in ({"v": 1.0}, {"v": -1}, {"v": 1 << 63}):
            with self.assertRaises(ValueError):
                SPLIT.canonical(value)

    def test_unported_actions_cannot_be_enabled_by_self_consistent_rehash(self):
        for action in ("accept_cue_documents", "synthesize_cue", "effective_mix", "effective_render",
                       "editable_pack", "clock", "chunk", "longform"):
            b, a, w = fixture()
            b["action"] = w["action"] = action
            with self.subTest(action=action), self.assertRaises(ValueError):
                SPLIT.validate_launch(*seal(b, a, w))

    def test_correlated_tokens_in_all_three_documents_refuse_stale_state(self):
        for target in ("wrapper", "inner", "audio"):
            b, a, w = fixture()
            if target == "wrapper":
                w["current_token"]["revision"] = "3"
            else:
                key = "context_json" if target == "inner" else "bindings_json"
                value = json.loads(w[key])
                value["before_token" if target == "inner" else "current_token"]["revision"] = "3"
                w[key] = json.dumps(value)
            with self.subTest(target=target), self.assertRaises(ValueError):
                SPLIT.validate_launch(*seal(b, a, w))
        b, a, w = fixture("prepare_effective_timeline")
        w["bindings_json"] = "{}"
        with self.assertRaises(ValueError):
            SPLIT.validate_launch(*seal(b, a, w))

    def test_exact_raw_authority_context_and_recipe_cannot_be_substituted(self):
        b, a, w = fixture()
        args = seal(b, a, w)
        for changed in ((args[0], args[1] + b" ", args[2]),
                        (args[0], args[1], args[2] + b" ")):
            with self.assertRaises(ValueError):
                SPLIT.validate_launch(*changed)
        b["execution_recipe_sha256"] = h("e")
        with self.assertRaises(ValueError):
            SPLIT.validate_launch(raw(b), args[1], args[2])
        # A changed exact inner string changes the WHOLE context and execution identity.
        previous = json.loads(args[0])["execution_recipe_sha256"]
        w["context_json"] = w["context_json"] + " "
        seal(b, a, w)
        self.assertNotEqual(previous, b["execution_recipe_sha256"])

    def test_roles_original_producer_protected_map_and_metadata_claims_remain_bound(self):
        for change in ("role", "producer", "protected", "model", "publication", "repin"):
            b, a, w = fixture()
            if change == "role":
                a["roles"]["original_python"]["path"] = "D:/processing/runtime/python.exe"
            elif change == "producer":
                a["original_producer_sha256"] = h("f")
            elif change == "protected":
                a["resolved_protected_inventory_sha256"] = h("f")
            else:
                a[{"model": "model_weights_verified", "publication": "active_graph_published",
                   "repin": "original_job_binding_changed"}[change]] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                SPLIT.validate_launch(*seal(b, a, w))
        b, a, w = fixture()
        b["roles"]["original_python"]["pin"]["size_bytes"] = True
        a["roles"] = copy.deepcopy(b["roles"])
        with self.assertRaises(ValueError):
            SPLIT.validate_launch(*seal(b, a, w))

    def test_dependency_order_role_and_exact_fixed_paths_are_authority_not_regex(self):
        for change in ("reorder", "duplicate", "role", "drive", "parent", "dot", "backslash"):
            b, a, w = fixture()
            rows = b["recipe_dependencies"]
            if change == "reorder":
                rows.reverse()
            elif change == "duplicate":
                rows.append(copy.deepcopy(rows[0]))
            elif change == "role":
                rows[0]["role"] = "processing_app"
            else:
                rows[0]["relative_path"] = {"drive": "D:/python.exe", "parent": "../python.exe",
                    "dot": "runtime/./python.exe", "backslash": "runtime\\python.exe"}[change]
            with self.subTest(change=change), self.assertRaises(ValueError):
                SPLIT.validate_launch(*seal(b, a, w))

    def test_actual_utf8_byte_bound_includes_whole_document(self):
        with self.assertRaises(ValueError):
            SPLIT.decode(b"1234", 3)
        b, a, w = fixture()
        b["context_pin"]["pin"]["size_bytes"] += 1
        b["execution_recipe_sha256"] = SPLIT.execution_recipe(b)
        with self.assertRaises(ValueError):
            SPLIT.validate_launch(raw(b), raw(a), raw(w))

    def test_independent_literal_native_python_utf8_domain_vector(self):
        # Literal vector computed separately from this module. It detects actual-NUL,
        # UTF8-versus-ASCII escaping, sorted keys and ordered role rows changing.
        vector = {"schema_version": 2, "kind": "recipe_vector", "path": "D:/café/Trúc Ly",
            "rows": [{"role": "original_runtime", "relative_path": "runtime/python.exe",
                "pin": {"sha256": h("a"), "size_bytes": 104952}},
                {"role": "processing_app",
                 "relative_path": "engine/dubflow/review/original_runtime_execution_v2.py",
                 "pin": {"sha256": h("b"), "size_bytes": 18823}}],
            "enabled": False, "context": None}
        self.assertEqual(SPLIT.execution_recipe(vector),
            "sha256:4071634ab6f7389c48ea06a12037b2897f090479d38bb344d06a29ddcd731abf")
        vector["rows"].reverse()
        self.assertNotEqual(SPLIT.execution_recipe(vector),
            "sha256:4071634ab6f7389c48ea06a12037b2897f090479d38bb344d06a29ddcd731abf")


if __name__ == "__main__":
    unittest.main()

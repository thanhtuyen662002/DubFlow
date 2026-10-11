"""Qualify only the reviewed SDK/runtime in a complete staged/installed bundle."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packaging.release.bootstrap import verify_bundle
from packaging.release.manifest import ReleaseManifest
from engine.dubflow.download.runtime import BUNDLE_HELPER, BUNDLE_PROFILE, provider_from_verified_bundle, source_runtime_health

SESSION_CASES = ["native_save_both_providers", "native_owner_reopen", "owned_factory_capability",
                 "provider_entropy_refusal", "ciphertext_tamper_refusal", "native_clear_both_providers",
                 "generic_session_refusal", "malformed_session_refusal", "plaintext_absent"]


def qualify_sdk_pages(*args, **kwargs):
    path = Path(__file__).resolve().parents[2] / "tests/source_adapter/qualify_sdk_pages.py"
    spec = importlib.util.spec_from_file_location("source_sdk_page_qualification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.qualify_sdk_pages(*args, **kwargs)


def qualify_source_sessions(*args, **kwargs):
    path = Path(__file__).with_name("source_session_smoke.py")
    spec = importlib.util.spec_from_file_location("source_session_qualification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.qualify_source_sessions(*args, **kwargs)


def qualify(root: Path, expected_source_sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source_sha):
        raise ValueError("expected source SHA is required")
    path = root / "release-manifest.json"
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("release manifest exceeds qualification budget")
    manifest = ReleaseManifest.from_mapping(json.loads(raw))
    if manifest.source_sha != expected_source_sha:
        raise ValueError("bundle source SHA does not match qualification source")
    # Includes signature policy, all runtime DLL/stdlib files, links, inventory
    # equality and bounded per-file hashes before any native SDK invocation.
    verify_bundle(root, manifest)
    health = source_runtime_health(root, artifacts=[item.to_dict() for item in manifest.artifacts])
    inventory = {item.path: item for item in manifest.artifacts}
    descriptor = root / BUNDLE_PROFILE
    with descriptor.open("rb") as stream:
        descriptor_bytes = stream.read(16 * 1024 + 1)
    if (len(descriptor_bytes) > 16 * 1024
            or hashlib.sha256(descriptor_bytes).hexdigest() != inventory[BUNDLE_PROFILE].sha256):
        raise ValueError("SDK descriptor differs from verified bundle inventory")
    profile = json.loads(descriptor_bytes)
    sdk_name = "runtime/source/" + profile["filename"]
    if sdk_name not in inventory or inventory[sdk_name].sha256 != health["sdk_sha256"]:
        raise ValueError("SDK archive is absent from the verified bundle inventory")
    generic = provider_from_verified_bundle(root, artifacts=[item.to_dict() for item in manifest.artifacts], provider_id="generic")
    native = generic._transport._sdk
    if (generic.provider_id != "generic" or native.python.resolve() != (root / "runtime/python.exe").resolve()
            or native.helper.resolve() != (root / BUNDLE_HELPER).resolve()
            or native.sdk_archive.resolve() != (root / sdk_name).resolve()
            or native.pins["helper"] != inventory[BUNDLE_HELPER].sha256
            or native.pins["sdk_archive"] != inventory[sdk_name].sha256
            or generic._stream_materializer.materializer is not generic._materializer):
        raise ValueError("generic factory did not bind the verified owned producers/materializer")
    pages = qualify_sdk_pages(root / sdk_name, root / BUNDLE_HELPER, descriptor,
        helper_sha256=inventory[BUNDLE_HELPER].sha256, descriptor_sha256=inventory[BUNDLE_PROFILE].sha256)
    if (pages["status"] != "passed" or len(pages["cases"]) != 6 or len(pages.get("generic_playlist_cases", [])) != 6
            or pages.get("generic_inspection", {}).get("status") != "passed"
            or pages.get("generic_missing_identity", {}).get("status") != "passed"
            or Path(pages["python"]).resolve() != (root / "runtime/python.exe").resolve()):
        raise ValueError("SDK page qualification did not run in the owned interpreter")
    manifest_hash = hashlib.sha256(raw).hexdigest()
    sessions = qualify_source_sessions(root, manifest_hash, expected_source_sha)
    if (sessions.get("status") != "passed"
            or sessions.get("scope") != "native-windows-protected-session-boundary"
            or sessions.get("source_sha") != expected_source_sha
            or sessions.get("manifest_sha256") != manifest_hash
            or sessions.get("supervisor_sha256") != inventory["app/bin/dubflow-supervisor.exe"].sha256
            or sessions.get("synthetic_credentials_only") is not True
            or type(sessions.get("network_requests")) is not int or sessions["network_requests"] != 0
            or sessions.get("cases") != SESSION_CASES):
        raise ValueError("native protected-session qualification is incomplete")
    return {"schema_version": 1, "source_sha": manifest.source_sha, "version": manifest.version,
            "release_channel": manifest.release_channel, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "scope": "source-sdk-runtime-health", "verified_release_tree": True, "health": health,
            "offline_sdk_pages": pages,
            "native_protected_sessions": sessions,
            "generic_factory": {"status": "passed", "provider_id": generic.provider_id,
                                "python": str(native.python), "producer_pins": native.pins},
            "live_source_acquisition": "not_run", "browser_session": "not_run",
            "durable_intake_enumeration": "not_run", "production_qualified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Qualification must not add unmanifested files to the verified release.
    try:
        args.report.resolve().relative_to(args.root.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("qualification report must be outside the bundle")
    report = qualify(args.root, args.expected_source_sha)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

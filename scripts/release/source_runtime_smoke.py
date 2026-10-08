"""Qualify only the reviewed SDK/runtime in a complete staged/installed bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from packaging.release.bootstrap import verify_bundle
from packaging.release.manifest import ReleaseManifest
from engine.dubflow.download.runtime import source_runtime_health


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
    return {"schema_version": 1, "source_sha": manifest.source_sha, "version": manifest.version,
            "release_channel": manifest.release_channel, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "scope": "source-sdk-runtime-health", "verified_release_tree": True, "health": health,
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

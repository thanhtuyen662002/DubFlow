"""Verify actual WebView2 initialization without mutating an installed release."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


def qualify_profile(root, data_root, manifest, verify_inventory, *, cache_timeout=30):
    root = Path(root).resolve(strict=True)
    data_root = Path(data_root).resolve(strict=True)
    cache = data_root / "control/webview2" / manifest.version.encode("ascii").hex()
    if cache.resolve().is_relative_to(root):
        raise RuntimeError("desktop browser data is inside the immutable runtime")
    # Include all files, even unexpected browser caches. Never exclude a cache
    # or alter the manifest to turn a failed installed inventory into a pass.
    verify_inventory(root, manifest)
    deadline = time.monotonic() + cache_timeout
    while True:
        browser = cache / "EBWebView"
        files = [path for path in browser.rglob("*") if path.is_file()] if browser.is_dir() else []
        if files:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("actual WebView2 user data was not created in the writable profile")
        time.sleep(0.25)
    verify_inventory(root, manifest)
    return {
        "schema_version": 1,
        "source_sha": manifest.source_sha,
        "version": manifest.version,
        "installed_root": str(root),
        "data_root": str(data_root),
        "webview_data_root": str(cache.resolve(strict=True)),
        "webview_file_count": len(files),
        "webview_initialized": True,
        "post_launch_inventory": "passed",
        "inventory_artifact_count": len(manifest.artifacts),
        "gui_usability": "NOT_RUN",
        "production_qualified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    interpreter = Path(sys.executable).resolve(strict=True)
    if interpreter != (root / "runtime/python.exe").resolve(strict=True):
        raise RuntimeError("post-launch inventory must run with the installed owned interpreter")
    # -I/-S admits only this release's application and dependency trees.
    # Signed release verification needs its owned cryptography dependency.
    sys.path[:0] = [str(root / "app"), str(root / "runtime/Lib/site-packages")]
    from packaging.release.bootstrap import verify_bundle
    from packaging.release.manifest import hash_file, load_manifest

    manifest_path = root / "release-manifest.json"
    manifest = load_manifest(manifest_path)
    if manifest.source_sha != args.expected_source_sha:
        raise RuntimeError("installed source does not match the expected CI source")
    result = qualify_profile(root, args.data_root, manifest, verify_bundle)
    result.update(interpreter=str(interpreter), manifest_sha256=hash_file(manifest_path))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

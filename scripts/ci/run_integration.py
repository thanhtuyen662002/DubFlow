from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from component_registry import (
    coverage_errors,
    load_registry,
    validate_registry,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "scripts" / "ci" / "component_registry.json"

# Selection logic and its inputs affect every registered deterministic component.
# Optional GPU/live-source/long-soak workflows remain separate lanes.
INTEGRATION_CONTROL_ROOTS = (
    "scripts/ci",
    "scripts/validate_governance.py",
    "tests/ci",
    ".github/workflows/pr-fast.yml",
    ".github/workflows/pr-integration.yml",
)


def run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    if argv[:2] == ["cargo", "metadata"]:
        run_metadata(argv)
        return
    subprocess.run(argv, cwd=ROOT, check=True)


def run_metadata(argv: list[str]) -> None:
    """Keep Cargo's machine JSON off the log; preserve bounded failure detail.

    Large single-line metadata is not a useful CI diagnostic. Capturing it to
    disk also leaves an explicit exit/result after that command, rather than a
    workflow log ending at its invocation without explaining the failure.
    """
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr, check=False)
        stdout_size = stdout.tell()
        stderr_size = stderr.tell()
        print(f"Cargo metadata exit={result.returncode} stdout_bytes={stdout_size} stderr_bytes={stderr_size}", flush=True)
        if result.returncode != 0:
            for name, stream, size, limit in (("stderr", stderr, stderr_size, 16384), ("stdout", stdout, stdout_size, 2048)):
                stream.seek(max(0, size - limit))
                detail = stream.read(limit).decode("utf-8", errors="replace")
                print(f"Cargo metadata {name} tail: {detail}", flush=True)
            result.check_returncode()


def changed_paths(base_sha: str | None) -> set[str] | None:
    if not base_sha or not base_sha.strip("0"):
        return None
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", f"{base_sha}...HEAD", "--"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print("Could not compute changed paths; running all registered components.")
        print(os.fsdecode(result.stderr))
        return None
    # NUL is the only delimiter that cannot be part of a Git pathname. Keep
    # bytes until splitting to avoid newline translation; fsdecode preserves
    # POSIX undecodable names. --no-renames retains both the old and new paths.
    return {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}


def component_affected(component: dict, changed: set[str] | None) -> bool:
    if changed is None:
        return True
    for root in (*INTEGRATION_CONTROL_ROOTS, *component["roots"]):
        root = root.rstrip("/")
        if any(path == root or path.startswith(root + "/") for path in changed):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", default=os.environ.get("BASE_SHA", ""))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    data = load_registry(REGISTRY_PATH)
    errors = validate_registry(data) + coverage_errors(ROOT, data)
    if errors:
        for error in errors:
            print("CI registry error:", error)
        return 1

    if args.validate_only:
        print("Integration registry validation passed.")
        return 0

    # Integration always re-validates the durable control plane first.
    run([sys.executable, "scripts/validate_governance.py"])

    changed = changed_paths(args.base_sha)
    selected = [
        c for c in data["components"] if component_affected(c, changed)
    ]

    if not data["components"]:
        print(
            "No product components exist yet; bootstrap Integration passes only "
            "because governance forbids unregistered product scopes."
        )
        return 0

    if not selected:
        print("No registered integration component is affected by this change.")
        return 0

    for component in selected:
        print(f"Running integration component: {component['id']}")
        for command in component["commands"]:
            run(command)

    print(f"Integration passed for {len(selected)} component(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

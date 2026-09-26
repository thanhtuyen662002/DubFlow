from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from component_registry import (
    coverage_errors,
    load_registry,
    validate_registry,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "scripts" / "ci" / "component_registry.json"


def run(argv: list[str]) -> None:
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, cwd=ROOT, check=True)


def changed_paths(base_sha: str | None) -> set[str] | None:
    if not base_sha or not base_sha.strip("0"):
        return None
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print("Could not compute changed paths; running all registered components.")
        print(result.stderr)
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def component_affected(component: dict, changed: set[str] | None) -> bool:
    if changed is None:
        return True
    for root in component["roots"]:
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

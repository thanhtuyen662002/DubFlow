from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CHILD_SCOPED_ROOTS = ("apps", "crates", "engine", "contracts", "packaging")
WHOLE_SCOPED_ROOTS = ("migrations",)


def load_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_rel_path(value: str) -> bool:
    p = Path(value)
    return bool(value) and not p.is_absolute() and ".." not in p.parts and "\\" not in value


def validate_registry(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"component registry schema_version must be {SCHEMA_VERSION}")

    components = data.get("components")
    if not isinstance(components, list):
        return errors + ["component registry components must be a list"]

    ids: set[str] = set()
    for idx, component in enumerate(components):
        prefix = f"components[{idx}]"
        if not isinstance(component, dict):
            errors.append(f"{prefix} must be an object")
            continue

        cid = component.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
        elif cid in ids:
            errors.append(f"duplicate component id: {cid}")
        else:
            ids.add(cid)

        roots = component.get("roots")
        if not isinstance(roots, list) or not roots:
            errors.append(f"{prefix}.roots must be a non-empty list")
        else:
            for root in roots:
                if not isinstance(root, str) or not _valid_rel_path(root):
                    errors.append(f"{prefix} has invalid root: {root!r}")

        commands = component.get("commands")
        if not isinstance(commands, list) or not commands:
            errors.append(f"{prefix}.commands must be a non-empty list")
        else:
            for command in commands:
                if (
                    not isinstance(command, list)
                    or not command
                    or not all(isinstance(arg, str) and arg for arg in command)
                ):
                    errors.append(
                        f"{prefix}.commands entries must be non-empty argv arrays"
                    )
    return errors


def discover_product_scopes(root: Path) -> set[str]:
    scopes: set[str] = set()
    for parent_name in CHILD_SCOPED_ROOTS:
        parent = root / parent_name
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            if child.is_dir():
                scopes.add(f"{parent_name}/{child.name}")

    for name in WHOLE_SCOPED_ROOTS:
        path = root / name
        if path.exists():
            scopes.add(name)
    return scopes


def registry_roots(data: dict[str, Any]) -> set[str]:
    roots: set[str] = set()
    for component in data.get("components", []):
        if isinstance(component, dict):
            for root in component.get("roots", []):
                if isinstance(root, str):
                    roots.add(root.rstrip("/"))
    return roots


def coverage_errors(root: Path, data: dict[str, Any]) -> list[str]:
    registered = registry_roots(data)
    errors: list[str] = []
    for scope in sorted(discover_product_scopes(root)):
        covered = any(
            scope == item
            or scope.startswith(item + "/")
            or item.startswith(scope + "/")
            for item in registered
        )
        if not covered:
            errors.append(
                f"product scope {scope!r} has no integration component registration"
            )
    return errors


def validate_current_repository(root: Path) -> list[str]:
    path = root / "scripts" / "ci" / "component_registry.json"
    if not path.is_file():
        return ["missing scripts/ci/component_registry.json"]
    data = load_registry(path)
    return validate_registry(data) + coverage_errors(root, data)


def run_self_tests() -> None:
    valid = {
        "schema_version": 1,
        "components": [
            {
                "id": "timeline",
                "roots": ["contracts/timeline", "crates/media-contracts"],
                "commands": [["python", "-c", "print('ok')"]],
            }
        ],
    }
    assert validate_registry(valid) == []

    duplicate = {
        "schema_version": 1,
        "components": [
            {"id": "x", "roots": ["crates/a"], "commands": [["true"]]},
            {"id": "x", "roots": ["crates/b"], "commands": [["true"]]},
        ],
    }
    assert any("duplicate component id" in e for e in validate_registry(duplicate))

    unsafe = {
        "schema_version": 1,
        "components": [
            {"id": "x", "roots": ["../outside"], "commands": [["true"]]}
        ],
    }
    assert any("invalid root" in e for e in validate_registry(unsafe))


if __name__ == "__main__":
    run_self_tests()
    print("Component registry self-tests passed.")

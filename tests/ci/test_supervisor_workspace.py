from __future__ import annotations

import difflib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "crates" / "job-supervisor"
WORKSPACE_MANIFEST = SUPERVISOR / "Cargo.toml"
LOCKFILE = SUPERVISOR / "Cargo.lock"
REGISTRY = ROOT / "scripts" / "ci" / "component_registry.json"


def workspace_members() -> set[str]:
    data = tomllib.loads(WORKSPACE_MANIFEST.read_text(encoding="utf-8"))
    workspace = data.get("workspace", {})
    members = workspace.get("members", [])
    if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
        raise AssertionError("workspace.members must be a list of strings")
    return {item.rstrip("/") for item in members}


def nested_crates() -> set[str]:
    return {child.name for child in SUPERVISOR.iterdir() if child.is_dir() and (child / "Cargo.toml").is_file()}


class SupervisorWorkspacePolicyTests(unittest.TestCase):
    def test_every_nested_crate_is_an_explicit_member(self) -> None:
        data = tomllib.loads(WORKSPACE_MANIFEST.read_text(encoding="utf-8"))
        workspace = data.get("workspace", {})
        actual = nested_crates()
        members = workspace_members()
        self.assertEqual(actual, members)
        excluded = {str(item).rstrip("/") for item in workspace.get("exclude", [])}
        self.assertFalse(actual & excluded, "supervisor crates must not be excluded from the parent workspace")

    def test_registered_supervisor_manifest_commands_target_members(self) -> None:
        members = workspace_members()
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        seen: set[str] = set()
        for component in data.get("components", []):
            for command in component.get("commands", []):
                for index, arg in enumerate(command[:-1]):
                    if arg != "--manifest-path":
                        continue
                    manifest = command[index + 1].replace("\\\\", "/")
                    prefix = "crates/job-supervisor/"
                    relative = manifest[len(prefix):] if manifest.startswith(prefix) else ""
                    if "/" in relative and manifest.endswith("/Cargo.toml"):
                        crate = relative.split("/", 1)[0]
                        seen.add(crate)
                        self.assertIn(crate, members, f"registry command targets non-member supervisor crate: {manifest}")
        self.assertTrue(seen, "expected at least one supervisor manifest command in the integration registry")

    def test_cargo_generated_lockfile_is_current(self) -> None:
        cargo = shutil.which("cargo")
        if cargo is None:
            self.skipTest("cargo is unavailable in this runtime")
        original = LOCKFILE.read_bytes()
        try:
            result = subprocess.run([cargo, "metadata", "--manifest-path", str(WORKSPACE_MANIFEST.relative_to(ROOT)), "--format-version", "1"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            generated = LOCKFILE.read_bytes()
        finally:
            LOCKFILE.write_bytes(original)
        if generated != original:
            diff = "".join(difflib.unified_diff(original.decode("utf-8").splitlines(keepends=True), generated.decode("utf-8").splitlines(keepends=True), fromfile="Cargo.lock (committed)", tofile="Cargo.lock (cargo metadata)"))
            self.fail("crates/job-supervisor/Cargo.lock is stale; regenerate it with Cargo:\n" + diff)


if __name__ == "__main__":
    unittest.main(verbosity=2)

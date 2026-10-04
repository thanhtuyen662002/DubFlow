"""Deterministic selector regressions; local Git only, no network or models."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import run_integration as selector


class ComponentSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.component = {"id": "tts", "roots": ["engine/dubflow/tts"]}

    def test_unknown_diff_selects_component(self) -> None:
        self.assertTrue(selector.component_affected(self.component, None))

    def test_empty_diff_selects_nothing(self) -> None:
        self.assertFalse(selector.component_affected(self.component, set()))

    def test_component_root_itself_is_affected(self) -> None:
        self.assertTrue(selector.component_affected(self.component, {"engine/dubflow/tts"}))

    def test_descendant_is_affected(self) -> None:
        self.assertTrue(selector.component_affected(self.component, {"engine/dubflow/tts/voice.py"}))

    def test_component_prefix_is_not_a_directory_boundary(self) -> None:
        self.assertFalse(selector.component_affected(self.component, {"engine/dubflow/tts-other/voice.py"}))

    def test_trailing_slash_root_is_supported(self) -> None:
        component = {"roots": ["engine/dubflow/tts/"]}
        self.assertTrue(selector.component_affected(component, {"engine/dubflow/tts/voice.py"}))

    def test_ci_control_changes_select_all_components(self) -> None:
        paths = (
            "scripts/ci/run_integration.py",
            "scripts/ci/component_registry.py",
            "scripts/ci/component_registry.json",
            "scripts/validate_governance.py",
            "tests/ci/test_integration_selection.py",
            ".github/workflows/pr-fast.yml",
            ".github/workflows/pr-integration.yml",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(selector.component_affected(self.component, {path}))

    def test_unrelated_docs_and_optional_workflows_are_not_global(self) -> None:
        paths = {
            "docs/production/README.md", ".github/workflows/watchdog.yml",
            ".github/workflows/soak-release.yml", "scripts/city/example.py",
            "tests/ci-extra/test_other.py", "scripts/validate_governance.py.bak",
        }
        self.assertFalse(selector.component_affected(self.component, paths))

    def test_unrelated_component_remains_unselected(self) -> None:
        self.assertFalse(selector.component_affected(self.component, {"engine/dubflow/asr/adapter.py"}))

    def test_main_executes_registered_commands_after_control_change(self) -> None:
        component = {**self.component, "commands": [["example-test-command"]]}
        data = {"schema_version": 1, "components": [component]}
        with patch.object(selector, "load_registry", return_value=data), \
             patch.object(selector, "coverage_errors", return_value=[]), \
             patch.object(selector, "changed_paths", return_value={"scripts/ci/component_registry.json"}), \
             patch.object(selector, "run") as run, \
             patch.object(sys, "argv", ["run_integration.py"]), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(selector.main(), 0)
        self.assertEqual(run.call_args_list[-1].args[0], ["example-test-command"])


class GitPathTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="dubflow-ci-selection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        environment = patch.dict(os.environ, env)
        environment.start()
        self.addCleanup(environment.stop)
        root_patch = patch.object(selector, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.git("init", "-q")
        self.git("config", "user.name", "DubFlow selector test")
        self.git("config", "user.email", "selector-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.quotePath", "true")
        self.git("config", "diff.renames", "true")
        self.git("commit", "--allow-empty", "-qm", "initial")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True,
            encoding="utf-8", errors="surrogateescape",
        )
        return result.stdout

    def write(self, name: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("identical content for rename detection\n", encoding="utf-8")

    def commit(self) -> str:
        self.git("add", "--all")
        self.git("commit", "-qm", "test change")
        return self.git("rev-parse", "HEAD").strip()

    def test_unicode_filename_is_not_git_quoted(self) -> None:
        name = "engine/dubflow/tts/giọng_việt.py"
        self.write(name)
        self.commit()
        self.assertEqual(selector.changed_paths(self.base), {name})

    @unittest.skipIf(os.name == "nt", "POSIX-only legal control-character filenames")
    def test_control_characters_are_not_line_separators(self) -> None:
        names = {"engine/dubflow/tts/tab\tname.py", "engine/dubflow/tts/new\nline.py", "engine/dubflow/tts/cr\rname.py"}
        for name in names:
            self.write(name)
        self.commit()
        self.assertEqual(selector.changed_paths(self.base), names)

    @unittest.skipIf(os.name == "nt", "Windows strips trailing spaces in filenames")
    def test_filename_whitespace_is_preserved(self) -> None:
        names = {"engine/dubflow/tts/ voice.py ", " leading-root/file.py"}
        for name in names:
            self.write(name)
        self.commit()
        self.assertEqual(selector.changed_paths(self.base), names)

    @unittest.skipUnless(os.name == "posix", "Raw undecodable filenames are POSIX-only")
    def test_undecodable_filename_does_not_crash_or_skip(self) -> None:
        name = os.fsdecode(b"engine/dubflow/tts/voice_\xff.py")
        self.write(name)
        self.commit()
        self.assertEqual(selector.changed_paths(self.base), {name})

    def test_cross_component_rename_includes_both_paths(self) -> None:
        old = "engine/dubflow/tts/old.py"
        new = "engine/dubflow/asr/new.py"
        self.write(old)
        base = self.commit()
        (self.root / new).parent.mkdir(parents=True, exist_ok=True)
        self.git("mv", old, new)
        self.commit()
        changed = selector.changed_paths(base)
        self.assertEqual(changed, {old, new})
        for namespace in ("tts", "asr"):
            self.assertTrue(selector.component_affected({"roots": [f"engine/dubflow/{namespace}"]}, changed))

    def test_renamed_control_file_still_selects_all(self) -> None:
        old, new = "scripts/ci/selector.py", "docs/selector.py"
        self.write(old)
        base = self.commit()
        (self.root / "docs").mkdir()
        self.git("mv", old, new)
        self.commit()
        self.assertTrue(selector.component_affected({"roots": ["engine/dubflow/tts"]}, selector.changed_paths(base)))

    def test_deleted_file_is_included(self) -> None:
        name = "engine/dubflow/tts/removed.py"
        self.write(name)
        base = self.commit()
        self.git("rm", name)
        self.commit()
        self.assertEqual(selector.changed_paths(base), {name})

    def test_real_empty_diff_is_empty(self) -> None:
        self.assertEqual(selector.changed_paths(self.base), set())

    def test_missing_and_zero_base_select_all(self) -> None:
        for base in (None, "", "0" * 40, "0" * 64):
            with self.subTest(base=base):
                self.assertIsNone(selector.changed_paths(base))

    def test_unresolvable_base_selects_all(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertIsNone(selector.changed_paths("a" * 40))

    def test_merge_base_excludes_other_branch_only_change(self) -> None:
        self.git("checkout", "-qb", "other-base")
        self.write("engine/dubflow/asr/base-only.py")
        other_base = self.commit()
        self.git("checkout", "-q", self.base)
        name = "engine/dubflow/tts/feature.py"
        self.write(name)
        self.commit()
        self.assertEqual(selector.changed_paths(other_base), {name})


if __name__ == "__main__":
    unittest.main()

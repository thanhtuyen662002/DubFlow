"""The actual registry must execute status repair tests for isolated edits."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/ci"))
import run_integration as selector


class SupervisorStatusSelectionTests(unittest.TestCase):
    def test_isolated_status_paths_execute_the_app_workspace_tests(self):
        data = selector.load_registry(selector.REGISTRY_PATH)
        for path in ("crates/job-supervisor/app/src/main.rs", "contracts/job_status/README.md"):
            with self.subTest(path=path), \
                    patch.object(selector, "changed_paths", return_value={path}), \
                    patch.object(selector, "run") as run, \
                    patch.object(sys, "argv", ["run_integration.py"]), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(selector.main(), 0)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn(["cargo", "test", "--manifest-path",
                    "crates/job-supervisor/Cargo.toml", "--workspace", "--locked"], commands)
        workspace = next(c for c in data["components"] if c["id"] == "supervisor-workspace")
        self.assertFalse(selector.component_affected(workspace, {"crates/job-supervisor/app-extra/source.rs"}))

    def test_isolated_native_guard_helper_executes_release_regressions(self):
        with patch.object(selector, "changed_paths", return_value={"scripts/release/production_smoke.py"}), \
                patch.object(selector, "run") as run, \
                patch.object(sys, "argv", ["run_integration.py"]), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(selector.main(), 0)
        self.assertIn(["python", "-m", "unittest", "discover", "-s", "tests/release", "-p", "test_*.py"],
            [call.args[0] for call in run.call_args_list])


if __name__ == "__main__":
    unittest.main()

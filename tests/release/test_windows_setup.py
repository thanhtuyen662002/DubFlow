"""Windows setup folder ownership and uninstall rules (#304).

Runs the functions of packaging/release/windows_setup.ps1 itself: Windows
PowerShell 5.1 on Windows (what setup uses), PowerShell 7 elsewhere.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

SETUP_SCRIPT = Path("packaging/release/windows_setup.ps1").resolve()
FUNCTIONS = ("Test-DubFlowInstall", "Test-OwnerMarker", "Test-InterruptedInstall", "Test-DubFlowRoot",
             "Write-OwnerMarker", "Remove-DubFlowFiles", "Test-InstallRoot", "Write-Uninstaller")


def _powershell() -> str | None:
    if os.name == "nt":
        inbox = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        if inbox.is_file():
            return str(inbox)
    return shutil.which("pwsh")


POWERSHELL = _powershell()


@unittest.skipUnless(POWERSHELL, "PowerShell is not available")
class WindowsSetupOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.base = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def run_setup(self, body: str):
        """Loads the setup's functions without running setup, then runs ``body``."""
        names = ", ".join(f"'{name}'" for name in FUNCTIONS)
        script = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
$RequiredFreeBytes = 1MB
# The repository copy has no BOM (setup adds one), so read it as UTF-8.
$ast = [System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText('{SETUP_SCRIPT}', [Text.Encoding]::UTF8), [ref]$null, [ref]$null)
foreach ($function in $ast.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true)) {{
    if (@({names}) -contains $function.Name) {{ . ([ScriptBlock]::Create($function.Extent.Text)) }}
}}
$result = & {{
{body}
}}
ConvertTo-Json -InputObject $result -Compress -Depth 4
"""
        driver = self.base / "driver.ps1"
        driver.write_text(script, encoding="utf-8-sig")
        completed = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.strip().splitlines()
        # Windows PowerShell 5.1 prints nothing for a $null result.
        return json.loads(lines[-1]) if lines else None

    def is_dubflow_root(self, root: Path) -> bool:
        return self.run_setup(f"Test-DubFlowRoot '{root}'")

    def make_install(self, root: Path, version: str = "1.2.3") -> None:
        (root / "versions" / version).mkdir(parents=True)
        (root / "versions" / version / "release-manifest.json").write_text("{}", encoding="utf-8")
        (root / "current.json").write_text(json.dumps(
            {"schema_version": 1, "current_version": version, "version_path": f"versions/{version}"}), encoding="utf-8")

    def tree(self, root: Path) -> list[str]:
        return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    def test_stray_versions_or_job_data_does_not_make_a_folder_dubflow(self) -> None:
        foreign = self.base / "Projects"
        (foreign / "versions" / "draft").mkdir(parents=True)
        (foreign / "keep.txt").write_text("user data", encoding="utf-8")
        self.assertFalse(self.is_dubflow_root(foreign))
        data_only = self.base / "Data"
        (data_only / "control" / "jobs").mkdir(parents=True)
        (data_only / "control" / "jobs.sqlite3").write_bytes(b"SQLite format 3\x00")
        self.assertFalse(self.is_dubflow_root(data_only))

    def test_a_pointer_that_does_not_name_an_installed_release_is_not_an_install(self) -> None:
        root = self.base / "DubFlow"
        self.make_install(root)
        self.assertTrue(self.is_dubflow_root(root))
        for pointer in ({"schema_version": 1, "current_version": "1.2.3", "version_path": "../elsewhere"},
                        {"schema_version": 1, "current_version": "..", "version_path": "versions/.."},
                        {"schema_version": 1, "current_version": "9.9.9", "version_path": "versions/9.9.9"}):
            (root / "current.json").write_text(json.dumps(pointer), encoding="utf-8")
            self.assertFalse(self.is_dubflow_root(root), pointer)

    def test_marker_recognises_only_the_folder_it_was_written_for(self) -> None:
        root = self.base / "DubFlow"
        root.mkdir()
        self.assertTrue(self.run_setup(f"Write-OwnerMarker '{root}'; Test-DubFlowRoot '{root}'"))
        moved = self.base / "Other"
        moved.mkdir()
        shutil.copy(root / ".dubflow-install.json", moved / ".dubflow-install.json")
        self.assertFalse(self.is_dubflow_root(moved))

    def test_interrupted_install_from_an_older_setup_is_resumable(self) -> None:
        root = self.base / "DubFlow"
        staging = root / "versions" / ".1.2.3.staging"
        staging.mkdir(parents=True)
        progress = {"schema_version": 1, "status": "failed", "version": "1.2.3",
                    "staging_path": str(staging), "completed": []}
        (root / "install-progress-1.2.3.json").write_text(json.dumps(progress), encoding="utf-8")
        self.assertTrue(self.is_dubflow_root(root))
        progress["staging_path"] = str(self.base / "elsewhere" / "versions" / ".1.2.3.staging")
        (root / "install-progress-1.2.3.json").write_text(json.dumps(progress), encoding="utf-8")
        self.assertFalse(self.is_dubflow_root(root))

    @unittest.skipUnless(os.name == "nt", "install folder rules are Windows paths")
    def test_setup_refuses_a_foreign_folder_and_leaves_it_unchanged(self) -> None:
        foreign = self.base / "Projects"
        (foreign / "versions" / "draft").mkdir(parents=True)
        (foreign / "control").mkdir()
        (foreign / "control" / "jobs.sqlite3").write_bytes(b"SQLite format 3\x00")
        (foreign / "sentinel.txt").write_text("user data", encoding="utf-8")
        before = self.tree(foreign)
        problem = self.run_setup(f"Test-InstallRoot '{foreign}'")
        self.assertIn("đã có tệp khác", problem)
        self.assertEqual(before, self.tree(foreign))
        self.assertEqual("user data", (foreign / "sentinel.txt").read_text(encoding="utf-8"))
        install = self.base / "DubFlow"
        self.make_install(install)
        empty = self.base / "New" / "DubFlow"
        self.assertIsNone(self.run_setup(f"Test-InstallRoot '{install}'"))
        self.assertIsNone(self.run_setup(f"Test-InstallRoot '{empty}'"))

    def test_uninstall_removes_only_dubflow_entries(self) -> None:
        root = self.base / "DubFlow"
        self.make_install(root)
        (root / "versions" / ".1.2.4.staging" / "app").mkdir(parents=True)
        (root / "versions" / "photos").mkdir()
        (root / "versions" / "photos" / "keep.jpg").write_bytes(b"photo")
        (root / "control" / "jobs").mkdir(parents=True)
        (root / "control" / "jobs.sqlite3").write_bytes(b"db")
        (root / "models").mkdir()
        (root / "models" / "model.bin").write_bytes(b"m")
        for name in ("install-state.json", "DubFlow.cmd", "DubFlow.exe", "DubFlow.ico", "install-progress-1.2.4.json"):
            (root / name).write_text("x", encoding="utf-8")
        (root / "notes.txt").write_text("user data", encoding="utf-8")
        failed = self.run_setup(f"Write-OwnerMarker '{root}'; Remove-DubFlowFiles '{root}'")
        self.assertEqual([], failed)
        self.assertEqual(["notes.txt", "versions", "versions/photos", "versions/photos/keep.jpg"], self.tree(root))

    def test_uninstall_of_a_plain_install_leaves_an_empty_folder(self) -> None:
        root = self.base / "DubFlow"
        self.make_install(root)
        (root / "control").mkdir()
        self.assertEqual([], self.run_setup(f"Write-OwnerMarker '{root}'; Remove-DubFlowFiles '{root}'"))
        self.assertEqual([], self.tree(root))

    def test_written_uninstaller_carries_the_same_rules(self) -> None:
        root = self.base / "DubFlow"
        root.mkdir()
        names = self.run_setup(f"""
Write-Uninstaller '{root}'
$errors = $null
$parsed = [System.Management.Automation.Language.Parser]::ParseFile('{root / "uninstall.ps1"}', [ref]$null, [ref]$errors)
if ($errors.Count -gt 0) {{ throw "uninstaller does not parse: $($errors[0].Message)" }}
@($parsed.FindAll({{ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }}, $true) | ForEach-Object {{ $_.Name }})
""")
        self.assertEqual(sorted(["Test-DubFlowInstall", "Test-OwnerMarker", "Test-InterruptedInstall",
                                 "Test-DubFlowRoot", "Remove-DubFlowFiles"]), sorted(names))
        script = (root / "uninstall.ps1").read_bytes()
        self.assertTrue(script.startswith(b"\xef\xbb\xbf"))
        text = script.decode("utf-8-sig")
        self.assertLess(text.index("if (-not (Test-DubFlowRoot $root))"), text.index("Remove-DubFlowFiles $root"))


if __name__ == "__main__":
    unittest.main()

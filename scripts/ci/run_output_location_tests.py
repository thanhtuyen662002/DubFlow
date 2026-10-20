"""Compile the exact desktop output policy without Tauri/platform UI dependencies."""
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dubflow-output-policy-") as directory:
        binary = Path(directory) / "output-policy-tests"
        subprocess.run([
            "rustc", "--edition=2021", "--test",
            str(ROOT / "apps/desktop/src-tauri/src/output_location.rs"),
            "-C", "debuginfo=0", "-o", str(binary),
        ], check=True, timeout=30, cwd=ROOT)
        subprocess.run([str(binary)], check=True, timeout=15, cwd=ROOT)


if __name__ == "__main__":
    main()

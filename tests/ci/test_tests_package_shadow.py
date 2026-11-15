"""#848: a third-party top-level ``tests`` package must not shadow the repository's tests.

argostranslate 1.11.0 installs ``site-packages/tests/__init__.py``. While the repository's
``tests`` was a namespace package, any regular ``tests`` package on ``sys.path`` won, even one
listed after the repository root, so ``python -m tests.mastering...`` failed on the packaged
runtime. With ``tests/__init__.py`` the repository root, which comes first, wins.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ThirdPartyTestsPackageTests(unittest.TestCase):
    def test_repository_tests_is_a_regular_package(self):
        self.assertTrue((ROOT / "tests" / "__init__.py").is_file())

    def test_a_bundled_tests_package_does_not_shadow_repository_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            bundled = Path(directory) / "tests"
            bundled.mkdir()
            (bundled / "__init__.py").write_text("", encoding="utf-8")  # same shape as argostranslate's wheel
            environment = {**os.environ, "PYTHONPATH": directory}
            script = "import tests, tests.mastering; print(tests.__file__)"
            done = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT, env=environment,
                                  capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(Path(done.stdout.strip()).resolve(), (ROOT / "tests" / "__init__.py").resolve())


if __name__ == "__main__":
    unittest.main()

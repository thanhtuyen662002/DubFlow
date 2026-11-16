"""#884 required CI lane: install the hash-pinned yt-dlp SDK runtime into a temporary folder and run the helper egress
tests against it. Fails when the SDK cannot be installed or when any SDK-backed test is skipped, so the real helper
and libcurl paths can never pass by being skipped.

``DUBFLOW_TEST_SDK_LIB`` already set: that folder is used instead of installing (local runs on other platforms).
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = Path(__file__).with_name("requirements-sdk-ci.txt")


def main() -> int:
    lib = os.environ.get("DUBFLOW_TEST_SDK_LIB", "")
    holder = None
    if not lib:
        holder = tempfile.TemporaryDirectory(prefix="dubflow-sdk-")
        lib = holder.name
        done = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                               "--only-binary=:all:", "--no-deps", "--require-hashes", "--target", lib,
                               "-r", str(REQUIREMENTS)])
        if done.returncode != 0:
            print("pinned SDK runtime could not be installed", file=sys.stderr)
            return 1
        os.environ["DUBFLOW_TEST_SDK_LIB"] = lib
    sys.path.insert(0, str(ROOT))
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.source_generic.test_native_egress")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        if holder is not None:
            holder.cleanup()
    sdk_skips = [(str(test), reason) for test, reason in result.skipped if "DUBFLOW_TEST_SDK_LIB" in reason]
    if sdk_skips:
        print("SDK-backed tests were skipped:", sdk_skips, file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

"""F3 (Phase 8): the engine holds its run's lock for its whole life; a second engine on the same run stops."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


def holder(work: Path, ready: Path) -> subprocess.Popen:
    code = textwrap.dedent(f"""
        import sys, time; sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from engine.dubflow.support import engine_lock
        engine_lock.hold(Path({str(work)!r}))
        Path({str(ready)!r}).write_text("x")
        time.sleep(60)
    """)
    return subprocess.Popen([sys.executable, "-B", "-c", code])


def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 30
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), "the holder never took the lock"


def try_hold(work: Path) -> str:
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from engine.dubflow.support import engine_lock
        try:
            engine_lock.hold(Path({str(work)!r}))
            print("held")
        except engine_lock.EngineBusy:
            print("busy")
    """)
    return subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60).stdout.strip()


class EngineLockTests(unittest.TestCase):
    def test_held_while_the_engine_lives_and_free_once_it_is_killed(self):
        with TemporaryDirectory() as tmp:
            work, ready = Path(tmp) / "work", Path(tmp) / "ready"
            process = holder(work, ready)
            try:
                wait_for(ready)
                self.assertEqual(try_hold(work), "busy")
            finally:
                process.kill()  # a hard kill: no cleanup code runs
                process.wait()
            self.assertEqual(try_hold(work), "held")

    def test_a_second_engine_on_a_busy_run_leaves_its_status_alone(self):
        with TemporaryDirectory() as tmp:
            out, ready = Path(tmp) / "run", Path(tmp) / "ready"
            work = out / ".dubflow-work"
            work.mkdir(parents=True)
            status = b'{"schema_version":1,"state":"RUNNING","done":1,"total":3}'
            (work / "status.json").write_bytes(status)
            process = holder(work, ready)
            try:
                wait_for(ready)
                for module in ("engine.dubflow.compilation", "engine.dubflow.product"):
                    args = [sys.executable, "-B", "-m", module, "--spec", str(work / "spec.json"), "--output-dir", str(out),
                            "--app-root", tmp, "--model-root", tmp, "--ffmpeg", "ffmpeg", "--ffprobe", "ffprobe"]
                    done = subprocess.run(args, capture_output=True, text=True, timeout=120, cwd=ROOT,
                                          env={**os.environ, "PYTHONPATH": str(ROOT)})
                    with self.subTest(module=module):
                        self.assertEqual(done.returncode, 3, done.stderr[-500:])
                        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1])["state"], "BUSY")
                        self.assertEqual((work / "status.json").read_bytes(), status)
            finally:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()

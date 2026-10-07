from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

from engine.dubflow.tts.adapter import TtsError
from engine.dubflow.tts.mimic3_native import clause_words, encode_words
from engine.dubflow.tts.native_process import NativeProcess


class FrontendTests(unittest.TestCase):
    def test_compound_dental_symbol_and_language_metadata(self) -> None:
        self.assertEqual(clause_words("t̪_ˈiɛ6_ (en)_v_i_(vi)_", 0x80028), [["t̪", "ˈ", "i", "ɛ", "6"], ["v", "i", "."]])

    def test_clause_and_sentence_punctuation(self) -> None:
        for terminator, punctuation in ((0x41014, ","), (0x4001E, ","), (0x82028, "."), (0x8302D, "."), (0x94000, None)):
            with self.subTest(terminator=terminator):
                self.assertEqual(clause_words("a_", terminator), [["a", punctuation]] if punctuation else [["a"]])

    def test_word_blanks_and_unknown_symbols_are_explicit(self) -> None:
        tokens = {"^": 1, "$": 2, "_": 0, "#": 5, "t̪": 30, "a": 14, ".": 4}
        ids, unknown = encode_words([["t̪", "a"], ["d", "."]], tokens)
        self.assertEqual(ids, [1, 0, 30, 0, 14, 0, 5, 0, 4, 0, 5, 0, 2])
        self.assertEqual(unknown, ("U0064",))


class NativeFailureTests(unittest.TestCase):
    def start(self, code: str, timeout: float = 1.0):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "child.py"
            script.write_text(code, encoding="utf-8")
            pack = SimpleNamespace(path=Path(directory), noise_scale=0.0, noise_scale_w=0.0)
            return NativeProcess(pack, timeout=timeout, command=[sys.executable, "-I", "-u", str(script)])

    def test_native_initialization_crash_is_typed_and_parent_survives(self) -> None:
        with self.assertRaises(TtsError) as raised:
            self.start("import os; os._exit(77)\n")
        self.assertEqual(raised.exception.code, "TTS_NATIVE_EXITED")

    def test_native_initialization_timeout_is_bounded(self) -> None:
        with self.assertRaises(TtsError) as raised:
            self.start("import time; time.sleep(60)\n", timeout=0.1)
        self.assertEqual(raised.exception.code, "TTS_NATIVE_TIMEOUT")

    def test_oversized_native_reply_is_rejected(self) -> None:
        with self.assertRaises(TtsError):
            self.start("import sys; sys.stdout.write('x'*5000+'\\n'); sys.stdout.flush()\n")

    def test_runtime_crash_after_health_is_typed(self) -> None:
        code = "import sys,json,os\nsys.stdin.readline()\nprint(json.dumps({'schema_version':1,'sequence':0,'ok':True,'frontend':'mimic3-word-blanks-v1'}),flush=True)\nsys.stdin.readline()\nos._exit(78)\n"
        bridge = self.start(code)
        try:
            with self.assertRaises(TtsError) as raised:
                bridge.generate("Xin chào", sid=0, speed=1.0)
            self.assertEqual(raised.exception.code, "TTS_NATIVE_EXITED")
        finally:
            bridge.close()


class NativeLifetimeTests(unittest.TestCase):
    def wait_for_file(self, path, process):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.is_file():
                return
            if process.poll() is not None:
                self.fail(f"parent exited early: {process.returncode}")
            time.sleep(0.02)
        self.fail("native operation did not start within its deadline")

    @unittest.skipIf(os.name == "nt", "Windows uses Job Object containment during DLL initialization")
    def test_stdin_lease_loss_stops_busy_initialization_and_inference(self):
        entrypoint = Path(__file__).resolve().parents[3] / "engine/dubflow/tts/mimic3_native.py"
        script = """
import json, runpy, sys, time
from pathlib import Path
namespace = runpy.run_path(sys.argv[1])
class Busy:
    def __init__(self, request):
        if sys.argv[3] == 'initialization':
            self.block()
    def block(self):
        Path(sys.argv[2]).write_text('busy')
        time.sleep(60)
    def generate(self, request):
        self.block()
namespace['main'].__globals__['NativeModel'] = Busy
namespace['main']()
"""
        for phase in ("initialization", "inference"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "busy"
                process = subprocess.Popen([sys.executable, "-I", "-u", "-c", script, str(entrypoint), str(marker), phase], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    requests = [{"sequence": 0}] + ([{"sequence": 1}] if phase == "inference" else [])
                    process.stdin.write(b"".join(json.dumps(request).encode() + b"\n" for request in requests))
                    process.stdin.flush()
                    self.wait_for_file(marker, process)
                    self.assertIsNone(process.poll(), "fixture must be inside the native operation before EOF")
                    process.stdin.close()
                    self.assertEqual(process.wait(timeout=3), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                    if not process.stdin.closed:
                        process.stdin.close()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object qualification")
    def test_hard_killed_worker_terminates_busy_native_process_and_descendant(self):
        self.assert_worker_death("inference")

    @unittest.skipUnless(os.name == "nt", "Windows Job Object qualification")
    def test_hard_killed_worker_terminates_busy_initialization_and_descendant(self):
        self.assert_worker_death("initialization")

    def assert_worker_death(self, phase):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        child_script = """
import json, os, runpy, subprocess, sys, time
from pathlib import Path
namespace = runpy.run_path(sys.argv[3])
class BusyModel:
    def __init__(self, request):
        descendant = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])
        Path(sys.argv[1], 'pids.json').write_text(json.dumps([os.getpid(), descendant.pid]))
        if sys.argv[2] == 'initialization':
            self.block()
    def block(self):
        Path(sys.argv[1], 'busy').write_text('busy')
        time.sleep(60)
    def generate(self, request):
        self.block()
namespace['main'].__globals__['NativeModel'] = BusyModel
namespace['main']()
"""
        parent_script = """
from pathlib import Path
import sys
from types import SimpleNamespace
from engine.dubflow.tts.native_process import NativeProcess
root = Path(sys.argv[1])
pack = SimpleNamespace(path=root, noise_scale=0.0, noise_scale_w=0.0)
entrypoint = Path.cwd()/'engine/dubflow/tts/mimic3_native.py'
bridge = NativeProcess(pack, timeout=10, command=[sys.executable, '-I', '-u', str(root/'child.py'), str(root), sys.argv[2], str(entrypoint)])
bridge.generate('Xin chao', sid=0, speed=1.0)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "child.py").write_text(child_script, encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-c", parent_script, str(root), phase], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            handles = []
            try:
                self.wait_for_file(root / "busy", process)
                for pid in json.loads((root / "pids.json").read_text()):
                    handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                    self.assertTrue(handle, f"native process {pid} must still be running")
                    handles.append(handle)
                    self.assertEqual(api.WaitForSingleObject(handle, 0), 258)
                process.kill()
                process.wait(timeout=3)
                for handle in handles:
                    self.assertEqual(api.WaitForSingleObject(handle, 3000), 0, "native tree survived hard worker death")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                for handle in handles:
                    api.CloseHandle(handle)
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()

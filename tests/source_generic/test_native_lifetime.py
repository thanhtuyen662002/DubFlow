"""Real Windows handles verify parent-death containment, without websites."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "nt", "native Windows Job Object qualification")
class NativeSourceLifetimeTests(unittest.TestCase):
    def test_parent_death_before_assignment_leaves_helper_without_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = Path(__file__).resolve().parents[2]
            helper = repo / "engine/dubflow/download/authenticated_native.py"
            ready = root / "ready.json"
            script = root / "parent.py"
            script.write_text('''
import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import engine.dubflow.download.generic.adapter as adapter
def delayed_assignment(process):
    pathlib.Path(sys.argv[3]).write_text(json.dumps(process.pid))
    time.sleep(120)
adapter.WindowsSourceJob = delayed_assignment
adapter.SubprocessYtDlpRunner().run_with_input([sys.executable, '-I', '-S', '-B', sys.argv[2], 'not-loaded-sdk.zip'], timeout_s=60, stdin_bytes=b'recorded-private-request')
''', encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-B", str(script), str(repo), str(helper), str(ready)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.WaitForSingleObject.restype = wintypes.DWORD
            api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            api.TerminateProcess.restype = wintypes.BOOL
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            handle = None
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "actual helper was not spawned")
                handle = api.OpenProcess(0x100000 | 0x1000 | 0x1, False, json.loads(ready.read_text()))
                self.assertTrue(handle)
                self.assertEqual(api.WaitForSingleObject(handle, 0), 0x102)
                process.kill()
                process.wait(timeout=5)
                self.assertEqual(api.WaitForSingleObject(handle, 5000), 0,
                                 "unassigned helper survived closed parent stdin")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if handle:
                    if api.WaitForSingleObject(handle, 0) == 0x102:
                        api.TerminateProcess(handle, 1)
                        api.WaitForSingleObject(handle, 5000)
                    api.CloseHandle(handle)

    def test_hard_parent_death_kills_helper_and_real_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "child.py"
            child.write_text('''
import json, os, pathlib, subprocess, sys, time
sys.stdin.buffer.read()  # SDK handshake: no work before parent job assignment.
descendant = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(120)'])
pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(), descendant.pid]))
time.sleep(120)
''', encoding="utf-8")
            parent = root / "parent.py"
            repo = Path(__file__).resolve().parents[2]
            parent.write_text('''
import sys
sys.path.insert(0, sys.argv[1])
from engine.dubflow.download.generic import SubprocessYtDlpRunner
SubprocessYtDlpRunner().run_with_input([sys.executable, '-B', sys.argv[2], sys.argv[3]], timeout_s=60, stdin_bytes=b'recorded-private-request')
''', encoding="utf-8")
            pids = root / "pids.json"
            process = subprocess.Popen([sys.executable, "-B", str(parent), str(repo), str(child), str(pids)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.WaitForSingleObject.restype = wintypes.DWORD
            api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            api.TerminateProcess.restype = wintypes.BOOL
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle.restype = wintypes.BOOL
            handles = []
            try:
                deadline = time.monotonic() + 10
                while not pids.exists() and time.monotonic() < deadline and process.poll() is None:
                    time.sleep(0.05)
                self.assertTrue(pids.exists(), "actual helper/descendant did not initialize")
                for pid in json.loads(pids.read_text()):
                    handle = api.OpenProcess(0x100000 | 0x1000 | 0x1, False, pid)
                    self.assertTrue(handle, "missing live native process handle")
                    handles.append(handle)
                    self.assertEqual(api.WaitForSingleObject(handle, 0), 0x102)
                process.kill()
                process.wait(timeout=5)
                for handle in handles:
                    self.assertEqual(api.WaitForSingleObject(handle, 5000), 0,
                                     "source native process survived hard parent death")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for handle in handles:
                    if api.WaitForSingleObject(handle, 0) == 0x102:
                        api.TerminateProcess(handle, 1)
                        api.WaitForSingleObject(handle, 5000)
                    api.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()

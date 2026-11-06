"""NVENC is chosen only when a real test encode succeeds; the CPU encoders always remain."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engine.dubflow import resources  # noqa: E402
from engine.dubflow.media import encoders  # noqa: E402


def _exits(code: int):
    """A stand-in for FFmpeg's test encode that exits with ``code``."""
    return mock.patch.object(encoders.subprocess, "run", return_value=subprocess.CompletedProcess([], code, b"", b""))


class ChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        encoders._CACHE.clear()
        self.ffmpeg = Path(tempfile.mkdtemp()) / "ffmpeg.exe"

    def test_a_working_test_encode_puts_nvenc_first(self):
        with _exits(0) as run:
            self.assertEqual(encoders.h264_encoders(self.ffmpeg), ("h264_nvenc", "h264_mf", "libopenh264"))
            self.assertEqual(encoders.h264_encoders(self.ffmpeg), ("h264_nvenc", "h264_mf", "libopenh264"))
        self.assertEqual(run.call_count, 1)  # Cached per executable.
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-hwaccel") + 1], "cuda")
        self.assertEqual(argv[argv.index("-c:v") + 1], "h264_nvenc")

    def test_a_failed_test_encode_or_missing_ffmpeg_keeps_the_cpu_encoders(self):
        with _exits(1):
            self.assertEqual(encoders.h264_encoders(self.ffmpeg), ("h264_mf", "libopenh264"))
        encoders._CACHE.clear()
        self.assertEqual(encoders.h264_encoders(self.ffmpeg), ("h264_mf", "libopenh264"))  # Real call: no such file.

    def test_cpu_choice_skips_the_probe(self):
        with _exits(0) as run:
            self.assertEqual(encoders.h264_encoders(self.ffmpeg, "cpu"), encoders.CPU_ENCODERS)
            with mock.patch.dict(os.environ, {"DUBFLOW_ACCELERATOR": "cpu"}):
                self.assertEqual(encoders.h264_encoders(self.ffmpeg), encoders.CPU_ENCODERS)
        run.assert_not_called()


class ArgumentTests(unittest.TestCase):
    def test_nvenc_rate_control_and_gpu_decode(self):
        args = encoders.rate_args("h264_nvenc", "8M")
        self.assertEqual(args[:4], ["-c:v", "h264_nvenc", "-b:v", "8M"])
        self.assertEqual(args[args.index("-maxrate") + 1], "12000000")
        self.assertEqual(args[args.index("-rc") + 1], "vbr")
        self.assertEqual(encoders.decode_args("h264_nvenc"), ["-hwaccel", "cuda"])
        self.assertEqual(encoders.decode_args("h264_mf"), [])

    def test_cpu_encoders_keep_their_existing_arguments(self):
        self.assertEqual(encoders.rate_args("h264_mf", 3_000_000), ["-c:v", "h264_mf", "-b:v", "3000000"])
        self.assertEqual(encoders.rate_args("libopenh264", 2_000_000),
                         ["-c:v", "libopenh264", "-b:v", "2000000", "-maxrate", "3000000", "-bufsize", "4000000"])


class ThreadBudgetTests(unittest.TestCase):
    def test_parts_rendered_at_once_share_the_machine_even_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch("engine.dubflow.resources.machine_threads", return_value=12):
            os.environ.pop("DUBFLOW_THREADS", None)
            self.assertEqual(resources.part_thread_args(4), ["-threads", "3", "-filter_threads", "3"])
            os.environ["DUBFLOW_THREADS"] = "4"
            self.assertEqual(resources.part_thread_args(3), ["-threads", "1", "-filter_threads", "1"])

    def test_unset_keeps_each_stage_default_and_ffmpeg_auto(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DUBFLOW_THREADS", None)
            self.assertIsNone(resources.configured())
            self.assertEqual(resources.cpu_threads(4), min(4, resources.machine_threads()))
            self.assertEqual(resources.ffmpeg_thread_args(), [])

    def test_all_and_a_number_bound_every_stage(self):
        machine = resources.machine_threads()
        with mock.patch.dict(os.environ, {"DUBFLOW_THREADS": "all"}):
            self.assertEqual(resources.cpu_threads(4), machine)
        with mock.patch.dict(os.environ, {"DUBFLOW_THREADS": "1"}):
            self.assertEqual(resources.cpu_threads(8), 1)
            self.assertEqual(resources.ffmpeg_thread_args(), ["-threads", "1", "-filter_threads", "1"])
        with mock.patch.dict(os.environ, {"DUBFLOW_THREADS": str(machine + 50)}):
            self.assertEqual(resources.configured(), machine)
        with mock.patch.dict(os.environ, {"DUBFLOW_THREADS": "nonsense"}):
            self.assertIsNone(resources.configured())

    @unittest.skipUnless(os.environ.get("DUBFLOW_TEST_FFMPEG"), "set DUBFLOW_TEST_FFMPEG to a real ffmpeg.exe to run")
    def test_the_shipped_ffmpeg_accepts_the_thread_options(self):
        result = subprocess.run([os.environ["DUBFLOW_TEST_FFMPEG"], "-hide_banner", "-v", "error", "-nostdin",
                                 "-f", "lavfi", "-i", "testsrc=s=320x240:d=0.5", "-vf", "scale=160:-2",
                                 *encoders.rate_args("libopenh264", 1_000_000), "-threads", "2", "-filter_threads", "2",
                                 "-filter_complex_threads", "2", "-f", "null", "-"], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-400:])


if __name__ == "__main__":
    unittest.main()

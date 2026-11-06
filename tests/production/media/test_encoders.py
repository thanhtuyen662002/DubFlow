"""NVENC is chosen only when a real test encode succeeds; the CPU encoders always remain."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engine.dubflow.media import encoders  # noqa: E402


def _fake_ffmpeg(directory: Path, exit_code: int) -> Path:
    script = directory / f"ffmpeg-{exit_code}"
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@unittest.skipIf(os.name == "nt", "uses a POSIX shell script as a stand-in FFmpeg")
class ChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        encoders._CACHE.clear()
        self.root = Path(tempfile.mkdtemp())

    def test_a_working_test_encode_puts_nvenc_first(self):
        self.assertEqual(encoders.h264_encoders(_fake_ffmpeg(self.root, 0)), ("h264_nvenc", "h264_mf", "libopenh264"))

    def test_a_failed_test_encode_or_missing_ffmpeg_keeps_the_cpu_encoders(self):
        self.assertEqual(encoders.h264_encoders(_fake_ffmpeg(self.root, 1)), ("h264_mf", "libopenh264"))
        self.assertEqual(encoders.h264_encoders(self.root / "absent.exe"), ("h264_mf", "libopenh264"))

    def test_cpu_choice_skips_the_probe(self):
        working = _fake_ffmpeg(self.root, 0)
        self.assertEqual(encoders.h264_encoders(working, "cpu"), encoders.CPU_ENCODERS)
        with mock.patch.dict(os.environ, {"DUBFLOW_ACCELERATOR": "cpu"}):
            self.assertEqual(encoders.h264_encoders(working), encoders.CPU_ENCODERS)
        self.assertNotIn(os.fspath(working), encoders._CACHE)


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


if __name__ == "__main__":
    unittest.main()

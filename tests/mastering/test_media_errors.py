"""#894 (Phase 8 F5): a source that cannot be used says why, in words the user can act on.

Before, a file with only sound, an MP4 cut part way and 200 KB of random bytes all said "<name> không có hình để phân
tích". Real files made with FFmpeg where a case can be made; FFprobe's answer is stood in for the rest."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.mastering import analysis as a

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


def make(path: Path, *inputs: str, extra: tuple[str, ...] = ()) -> Path:
    command = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    for source in inputs:
        command += ["-f", "lavfi", "-i", source]
    command += ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", *([] if "-t" in extra else ["-shortest"]), *extra, str(path)]
    subprocess.run(command, check=True, timeout=120)
    return path


def cut(path: Path, into: Path, share: int = 3) -> Path:
    into.write_bytes(path.read_bytes()[: path.stat().st_size // share])
    return into


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg is not installed")
class RealMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def why(self, path: Path) -> str:
        with self.assertRaises(a.AnalysisError) as raised:
            a.probe(Path(FFPROBE), path)
        return str(raised.exception)

    def test_good_sources_with_and_without_sound_are_read(self):
        both = make(self.root / "both.mp4", "testsrc2=s=320x240:d=6", "sine=f=440:d=6")
        silent = make(self.root / "silent.mkv", "testsrc2=s=320x240:d=6")
        fast = make(self.root / "fast.mp4", "testsrc2=s=320x240:d=6", extra=("-movflags", "+faststart"))
        self.assertTrue(a.probe(Path(FFPROBE), both).has_audio)
        self.assertFalse(a.probe(Path(FFPROBE), silent).has_audio)  # no sound is no error: the narration carries it
        self.assertEqual(a.probe(Path(FFPROBE), fast).duration_ms, 6000)
        # a picture that ends seconds before its sound is whole, not cut short (rc4 D1 kind, longer)
        early = make(self.root / "early.mkv", "testsrc2=s=320x240:d=3", "sine=f=440:d=9", extra=("-t", "9"))
        info = a.probe(Path(FFPROBE), early)
        self.assertLess(info.picture_ms or 0, info.duration_ms)

    def test_each_unusable_source_says_its_own_reason(self):
        sound = make(self.root / "sound.m4a", "sine=f=440:d=6")
        whole = make(self.root / "whole.mp4", "testsrc2=s=320x240:d=6", "sine=f=440:d=6")
        fast = make(self.root / "fast.mp4", "testsrc2=s=320x240:d=6", extra=("-movflags", "+faststart"))
        mkv = make(self.root / "whole.mkv", "testsrc2=s=320x240:d=6")
        noise = self.root / "noise.mp4"
        noise.write_bytes(os.urandom(200_000))
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        cases = {
            sound: "chỉ có âm thanh, không có hình",
            cut(whole, self.root / "cut.mp4"): "bị cắt cụt hoặc chưa tải xong (thiếu phần chỉ mục",
            cut(fast, self.root / "cut-fast.mp4"): "bị cắt cụt: file ghi dài 6 giây",
            cut(mkv, self.root / "cut.mkv"): "bị cắt cụt",
            noise: "bị hỏng hoặc không phải file video",
            empty: "là file rỗng (0 byte)",
            self.root / "gone.mp4": "Không tìm thấy gone.mp4",
            self.root: "là một thư mục",
        }
        if os.name == "nt":
            cases.pop(self.root)  # opening a folder on Windows is a permission error, not a folder error
        reasons = {}
        for path, expected in cases.items():
            with self.subTest(path=path.name):
                reasons[path.name] = self.why(path)
                self.assertIn(expected, reasons[path.name])
                self.assertNotIn("không có hình để phân tích", reasons[path.name])
        self.assertEqual(len(set(reasons.values())), len(reasons), "no two faults read alike")


class StoodInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.video = Path(self.tmp.name) / "clip.mp4"
        self.video.write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def answer(self, value, errors=b"", code=0):
        out = json.dumps(value).encode() if value is not None else b""
        return mock.patch.object(a.subprocess, "run", return_value=subprocess.CompletedProcess([], code, out, errors))

    def why(self) -> str:
        with self.assertRaises(a.AnalysisError) as raised:
            a.probe(Path("ffprobe"), self.video)
        return str(raised.exception)

    def test_a_picture_in_a_codec_this_ffmpeg_cannot_decode(self):
        for stream in ({"codec_type": "video", "codec_name": "none", "width": 0, "height": 0},
                       {"codec_type": "video", "width": 1920, "height": 1080}):
            with self.subTest(stream=stream), self.answer({"format": {"duration": "10"}, "streams": [stream]}):
                self.assertIn("dùng mã hóa", self.why())
        with self.answer({"format": {"duration": "10"}, "streams": [{"codec_type": "video", "codec_name": "prores_raw"}]}):
            self.assertIn("mã hóa prores_raw mà FFmpeg của DubFlow không đọc được", self.why())

    def test_cover_art_is_not_a_picture(self):
        art = {"codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500, "disposition": {"attached_pic": 1}}
        with self.answer({"format": {"duration": "10"}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}, art]}):
            self.assertIn("chỉ có âm thanh, không có hình", self.why())

    def test_permission_and_disk_errors_are_named(self):
        with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
            self.assertIn("Không có quyền đọc clip.mp4", self.why())
        with mock.patch("builtins.open", side_effect=OSError(5, "Input/output error")):
            self.assertIn("lỗi ổ đĩa hoặc mạng: Input/output error", self.why())
        with self.answer(None, b"clip.mp4: Permission denied", 1):
            self.assertIn("Không có quyền đọc clip.mp4", self.why())

    def test_ffprobe_that_hangs_or_cannot_start(self):
        with mock.patch.object(a.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffprobe", 60)):
            self.assertIn("quá lâu", self.why())
        with mock.patch.object(a.subprocess, "run", side_effect=FileNotFoundError("ffprobe")):
            self.assertIn("Không chạy được FFprobe", self.why())

    def test_random_bytes_named_mp4_are_damaged_not_cut_short(self):
        self.video.write_bytes(os.urandom(4096).replace(b"ftyp", b"xxxx"))
        with self.answer({}, b"[mov] moov atom not found\nclip.mp4: Invalid data found when processing input", 1):
            self.assertIn("bị hỏng hoặc không phải file video", self.why())


if __name__ == "__main__":
    unittest.main()

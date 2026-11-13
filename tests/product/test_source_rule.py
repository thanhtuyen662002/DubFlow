"""Issue #816: the worker's copy of the one source rule (Bán hàng and Tổng hợp), checked again when a spec is read."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.compilation import program
from engine.dubflow.product import job, render

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
CLIPS = render.IMAGE_SUFFIXES | render.VIDEO_SUFFIXES


def symlink(target: Path, link: Path, test: unittest.TestCase) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:  # Windows without Developer Mode or elevation
        test.skipTest(f"this system does not let the test make a symlink: {error}")


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # the real place: a temporary folder reached through a link (macOS /var) would be refused, rightly
        self.root = Path(os.path.realpath(self.directory.name))
        self.clip = self.root / "a.mp4"
        self.clip.write_bytes(b"clip")

    def refused(self, value, message: str, suffixes=CLIPS, name="video nguồn") -> None:
        with self.assertRaises(job.ProductError) as caught:
            job._plain_file(value, suffixes, name)
        self.assertEqual(caught.exception.message, message)


class PlainFileTests(Fixture):
    def test_a_real_file_anywhere_and_a_hard_link_are_sources(self):
        self.assertEqual(job._plain_file(str(self.clip), CLIPS, "video nguồn"), self.clip)
        hard = self.root / "hard.mp4"
        os.link(self.clip, hard)
        self.assertEqual(job._plain_file(str(hard), CLIPS, "video nguồn"), hard, "a hard link is the file itself")

    def test_traversal_relative_missing_folders_and_other_formats_are_refused(self):
        (self.root / "sub").mkdir()
        traversal = os.path.join(str(self.root), "sub", "..", "a.mp4")
        self.assertTrue(os.path.isfile(traversal), "the path names a file that exists: only the `..` refuses it")
        self.refused(traversal, "Đường dẫn video nguồn không hợp lệ: a.mp4")
        self.refused(os.path.join(str(self.root), ".", "a.mp4"), "Đường dẫn video nguồn không hợp lệ: a.mp4")
        self.refused("a.mp4", "Đường dẫn video nguồn không hợp lệ: a.mp4")
        self.refused(str(self.root / "missing.mp4"), "Không tìm thấy video nguồn: missing.mp4")
        (self.root / "folder.mp4").mkdir()
        self.refused(str(self.root / "folder.mp4"), "Video nguồn phải là tệp thật trên máy, không phải lối tắt: folder.mp4")
        (self.root / "a.flv").write_bytes(b"clip")
        self.refused(str(self.root / "a.flv"), "Không mở được video nguồn: a.flv")
        for bad in (None, "", 3, "x" * 1025):
            self.refused(bad, "video nguồn không hợp lệ")

    def test_a_link_is_refused(self):
        link = self.root / "clip-link.mp4"
        symlink(self.clip, link, self)
        self.refused(str(link), "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mp4")
        self.assertEqual(self.clip.read_bytes(), b"clip")

    def test_a_file_below_a_linked_folder_is_refused(self):
        real = self.root / "real"
        (real / "deeper").mkdir(parents=True)
        (real / "deeper" / "b.mp4").write_bytes(b"clip")
        alias = self.root / "alias"
        symlink(real, alias, self)
        self.assertTrue((alias / "deeper" / "b.mp4").is_file(), "reachable through the link")
        self.refused(str(alias / "deeper" / "b.mp4"),
                     "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.mp4")
        self.assertEqual(job._plain_file(str(real / "deeper" / "b.mp4"), CLIPS, "video nguồn"), real / "deeper" / "b.mp4",
                         "the same file at its real place")

    def test_a_name_surrogate_reparse_point_is_a_link_and_another_reparse_point_is_the_file(self):
        # Windows' lstat view, without Windows: a junction or symlink carries a name-surrogate tag; a OneDrive placeholder does not
        def info(attributes: int, tag: int):
            return type("Info", (), {"st_mode": 0o100644, "st_file_attributes": attributes, "st_reparse_tag": tag})()
        self.assertTrue(job._redirect(info(0x400, 0xA0000003)), "a junction (IO_REPARSE_TAG_MOUNT_POINT)")
        self.assertTrue(job._redirect(info(0x400, 0xA000000C)), "a symlink (IO_REPARSE_TAG_SYMLINK)")
        self.assertFalse(job._redirect(info(0x400, 0x9000601A)), "a cloud file (IO_REPARSE_TAG_CLOUD_6)")
        self.assertFalse(job._redirect(info(0x400, 0x80000013)), "a deduplicated file (IO_REPARSE_TAG_DEDUP)")
        self.assertFalse(job._redirect(info(0, 0)))


class SpecTests(Fixture):
    def write_spec(self, **changes) -> Path:
        value = {"schema_version": 1, "name": "Nồi chiên", "price": "", "voice_id": "vi-test", "variants": ["Xin chào."],
                 "visuals": [str(self.clip)]}
        value.update(changes)
        path = self.root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def assert_refused(self, spec: Path, message: str) -> None:
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(spec)
        self.assertEqual(caught.exception.message, message)

    def test_a_linked_clip_photo_or_music_in_a_spec_is_refused(self):
        photo, music = self.root / "a.jpg", self.root / "a.mp3"
        photo.write_bytes(b"jpg")
        music.write_bytes(b"mp3")
        self.assertEqual(job.load_spec(self.write_spec(visuals=[str(self.clip), str(photo)], music=str(music))).music, music)
        for target, name in ((self.clip, "clip-link.mp4"), (photo, "photo-link.jpg"), (music, "music-link.mp3")):
            symlink(target, self.root / name, self)
        self.assert_refused(self.write_spec(visuals=[str(self.clip), str(self.root / "clip-link.mp4")]),
                            "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mp4")
        self.assert_refused(self.write_spec(visuals=[str(self.root / "photo-link.jpg")]),
                            "Ảnh phải là tệp thật trên máy, không phải lối tắt: photo-link.jpg")
        self.assert_refused(self.write_spec(music=str(self.root / "music-link.mp3")),
                            "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: music-link.mp3")
        hard = self.root / "hard.mp4"
        os.link(self.clip, hard)
        self.assertEqual(job.load_spec(self.write_spec(visuals=[str(hard)])).visuals, (hard,))

    def test_a_source_swapped_for_a_link_after_the_desktop_checked_it_is_refused(self):
        # S6: the desktop's check passed; the file is replaced by a link before the worker reads the spec
        spec = self.write_spec()
        self.assertEqual(job._plain_file(str(self.clip), CLIPS, "video nguồn"), self.clip)
        other = self.root / "other.mp4"
        other.write_bytes(b"other")
        self.clip.unlink()
        symlink(other, self.clip, self)
        self.assert_refused(spec, "Video nguồn phải là tệp thật trên máy, không phải lối tắt: a.mp4")
        # and a folder on the way swapped for a link
        real = self.root / "real"
        real.mkdir()
        (real / "b.mp4").write_bytes(b"clip")
        moved = self.root / "moved"
        spec = self.write_spec(visuals=[str(moved / "b.mp4")])
        real.rename(moved)
        self.assertEqual(job.load_spec(spec).visuals, (moved / "b.mp4",))
        moved.rename(real)
        symlink(real, moved, self)
        self.assert_refused(spec, "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.mp4")

    def test_a_traversal_in_a_spec_is_refused(self):
        (self.root / "sub").mkdir()
        self.assert_refused(self.write_spec(visuals=[os.path.join(str(self.root), "sub", "..", "a.mp4")]),
                            "Đường dẫn video nguồn không hợp lệ: a.mp4")

    @unittest.skipUnless(FFMPEG, "needs FFmpeg")
    def test_a_render_only_reads_its_sources(self):
        photo, clip = self.root / "a.png", self.root / "b.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1", "-frames:v", "1", str(photo)], check=True)
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1:r=25", str(clip)], check=True)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (photo, clip)}
        spec = job.load_spec(self.write_spec(visuals=[str(photo), str(clip)]))
        self.assertEqual(spec.visuals, (photo, clip), "the spec keeps the checked paths as given")
        result = job.run(spec, self.root / "DubFlow Output" / "run", speaker=lambda status: FakeSpeaker(), probe=lambda path: 1000,
                         ffmpeg=Path(FFMPEG), encoder="mpeg4")
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (photo, clip)}, before)


class FakeSpeaker:
    sample_rate = 48000

    def speak(self, text):
        return [0.1] * (480 * len(text))

    def close(self):
        pass


class CompilationSourceTests(Fixture):
    """Tổng hợp's worker (``program._source``) reads sources by the same rule and the same texts."""

    def source(self, video: str):
        return program._source({"video": video}, "Mục 1, nguồn 1")

    def assert_refused(self, video: str, message: str) -> None:
        with self.assertRaises(program.CompilationError) as caught:
            self.source(video)
        self.assertEqual(caught.exception.message, message)

    def test_real_and_hard_linked_sources_are_kept(self):
        self.assertEqual(self.source(str(self.clip)).video, self.clip)
        os.link(self.clip, self.root / "hard.mp4")
        self.assertEqual(self.source(str(self.root / "hard.mp4")).video, self.root / "hard.mp4")

    def test_links_folder_links_and_traversal_are_refused(self):
        (self.root / "sub").mkdir()
        self.assert_refused(os.path.join(str(self.root), "sub", "..", "a.mp4"), "Đường dẫn video nguồn không hợp lệ: a.mp4")
        self.assert_refused(str(self.root / "missing.mp4"), "Không tìm thấy video nguồn: missing.mp4")
        symlink(self.clip, self.root / "clip-link.mkv", self)
        self.assert_refused(str(self.root / "clip-link.mkv"), "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv")
        (self.root / "sub" / "b.mp4").write_bytes(b"clip")
        symlink(self.root / "sub", self.root / "alias", self)
        self.assert_refused(str(self.root / "alias" / "b.mp4"),
                            "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: b.mp4")


if __name__ == "__main__":
    unittest.main()

"""Issue #816: the worker's copy of the one source rule (Bán hàng and Tổng hợp), checked again when a spec is read."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.compilation import job as compilation_job, program
from engine.dubflow.package import job as package_job
from engine.dubflow.product import job, render

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
CLIPS = render.IMAGE_SUFFIXES | render.VIDEO_SUFFIXES


def symlink(target: Path, link: Path, test: unittest.TestCase) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as error:  # Windows without Developer Mode or elevation
        # the hosted runners can always make one: there a refusal fails, so the link cases never pass without running
        if os.environ.get("CI"):
            test.fail(f"CI must be able to make a symlink: {error}")
        test.skipTest(f"this system does not let the test make a symlink: {error}")


def junction(target: Path, link: Path, test: unittest.TestCase) -> None:
    """A real Windows directory junction (no privilege needed): what Explorer, ``mklink /J`` and installers make."""
    if os.name != "nt":
        test.skipTest("a directory junction needs Windows")
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError) as first:
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], stdin=subprocess.DEVNULL, capture_output=True)
        if made.returncode:
            test.skipTest(f"Windows does not let the test make a junction: {first}; mklink: {made.stderr!r}")
    test.addCleanup(lambda: os.path.lexists(link) and os.rmdir(link))  # removes the junction only, never its target


def library_entry(library: Path, data: bytes, extension: str = "png", *, listed: bool = True) -> Path:
    """A background as the desktop's `add_to_library` keeps one: `<sha256>.<extension>`, listed in `index.json`."""
    library.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    path = library / f"{digest}.{extension}"
    path.write_bytes(data)
    index = library / "index.json"
    items = json.loads(index.read_text(encoding="utf-8"))["items"] if index.exists() else []
    if listed:
        items.insert(0, {"id": digest, "extension": extension, "name": f"picked.{extension}", "width": 1, "height": 1})
    index.write_text(json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")
    return path


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


class SpecFixture(Fixture):
    def write_spec(self, **changes) -> Path:
        value = {"schema_version": 1, "name": "Nồi chiên", "price": "", "voice_id": "vi-test", "variants": ["Xin chào."],
                 "visuals": [str(self.clip)]}
        value.update(changes)
        path = self.root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def assert_refused(self, spec: Path, message: str, library: Path | None = None) -> None:
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(spec) if library is None else job.load_spec(spec, library)
        self.assertEqual(caught.exception.message, message)


class SpecTests(SpecFixture):
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

    def test_a_background_is_the_apps_library_copy_or_a_real_file_of_the_users(self):
        # Issue #816 review: the background followed links and `..`. A library copy is the app's own file, so the folders
        # above its library are not looked at (the data folder may be reached through a junction): only for a copy that
        # is proven to be the app's, against the library folder the desktop names (`--library-dir`).
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        self.assertEqual(job.load_spec(self.write_spec(background=str(kept)), library).background, kept)
        self.assertEqual(job.load_spec(self.write_spec(background=str(kept))).background, kept, "a real file at its real place")
        alias = self.root / "alias"
        symlink(self.root / "data", alias, self)
        through = alias / "backgrounds" / kept.name
        self.assertEqual(job.load_spec(self.write_spec(background=str(through)), alias / "backgrounds").background, through,
                         "a data folder reached through a junction keeps its backgrounds")
        below = f"Ảnh nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {kept.name}"
        # the allowance is the named library's alone: no library, the library at its other spelling, another folder
        for named in (None, library, self.root / "data", alias):
            with self.subTest(library=named):
                self.assert_refused(self.write_spec(background=str(through)), below, named)
        self.assert_refused(self.write_spec(background=os.path.join(str(library), "..", "backgrounds", kept.name)),
                            f"Đường dẫn ảnh nền không hợp lệ: {kept.name}", library)
        self.assert_refused(self.write_spec(background=os.path.join(str(alias), "backgrounds", ".", kept.name)),
                            f"Đường dẫn ảnh nền không hợp lệ: {kept.name}", alias / "backgrounds")
        symlink(kept, self.root / "bg-link.png", self)
        self.assert_refused(self.write_spec(background=str(self.root / "bg-link.png")),
                            "Ảnh nền phải là tệp thật trên máy, không phải lối tắt: bg-link.png", library)
        symlink(library, self.root / "library-link", self)
        self.assert_refused(self.write_spec(background=str(self.root / "library-link" / kept.name)), below, self.root / "library-link")
        self.assertEqual(kept.read_bytes(), b"png")

    def test_a_background_of_the_users_below_a_linked_folder_is_refused(self):
        # the worker's bypass: every background skipped the folders above its own, so a user's picture below a linked
        # folder passed. Only the app's proven copy skips them now.
        (self.root / "real" / "pictures").mkdir(parents=True)
        own = self.root / "real" / "pictures" / "own.png"
        own.write_bytes(b"png")
        library = self.root / "real" / "backgrounds"
        kept = library_entry(library, b"kept")
        symlink(self.root / "real", self.root / "alias", self)
        through = self.root / "alias" / "pictures" / "own.png"
        self.assertTrue(through.is_file(), "reachable through the link")
        message = "Ảnh nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: own.png"
        for named in (None, library, self.root / "alias" / "backgrounds", self.root / "alias" / "pictures"):
            with self.subTest(library=named):
                self.assert_refused(self.write_spec(background=str(through)), message, named)
        self.assertEqual(job.load_spec(self.write_spec(background=str(own)), library).background, own, "the same file at its real place")
        self.assertEqual(job.load_spec(self.write_spec(background=str(self.root / "alias" / "backgrounds" / kept.name)),
                                       self.root / "alias" / "backgrounds").background, self.root / "alias" / "backgrounds" / kept.name)

    def test_a_file_named_like_a_library_entry_is_a_file_of_the_users(self):
        # 64 hex characters and an image extension are a name, not a proof
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        digest = hashlib.sha256(b"png").hexdigest()
        cases = {
            "outside the library, the right bytes": self.root / "data" / "pictures" / f"{digest}.png",
            "outside the library, any bytes": self.root / "data" / f"{'a' * 64}.png",
            "in the library, not listed, other bytes": library / f"{'b' * 64}.png",
            "in the library, not listed, the right bytes": library_entry(library, b"placed", listed=False),
            "in the library, listed, other bytes": library_entry(library, b"listed"),
            "in the library, listed under another extension": library / f"{digest}.jpg",
            "in a folder below the library": library / "deeper" / f"{digest}.png",
        }
        for path in cases.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        for label in ("outside the library, the right bytes", "in the library, listed under another extension", "in a folder below the library"):
            cases[label].write_bytes(b"png")
        for label in ("outside the library, any bytes", "in the library, not listed, other bytes", "in the library, listed, other bytes"):
            cases[label].write_bytes(b"other bytes")
        symlink(self.root / "data", self.root / "alias", self)
        through = self.root / "alias" / "backgrounds"
        self.assertEqual(job._library_file(str(kept), library), kept)
        self.assertEqual(job._library_file(str(through / kept.name), through), through / kept.name)
        for label, path in cases.items():
            with self.subTest(label):
                self.assertIsNone(job._library_file(str(path), library), "not the app's proven copy")
                self.assertEqual(job.load_spec(self.write_spec(background=str(path)), library).background, path,
                                 "a real file below real folders: accepted, as the user's")
                linked = self.root / "alias" / path.relative_to(self.root / "data")
                self.assertTrue(linked.is_file())
                self.assert_refused(self.write_spec(background=str(linked)),
                                    f"Ảnh nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {path.name}", through)

    def test_the_proof_reads_the_index_and_the_bytes(self):
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        self.assertEqual(job._library_file(str(kept), library), kept)
        self.assertIsNone(job._library_file(str(kept), None), "no library named")
        self.assertIsNone(job._library_file(str(kept), Path("backgrounds")), "a relative library")
        self.assertIsNone(job._library_file(str(kept).upper(), library), "another spelling")
        self.assertIsNone(job._library_file(str(library / f"{'c' * 64}.png"), library), "nothing there")
        self.assertIsNone(job._library_file(str(library / "index.json"), library))
        upper = library / f"{kept.stem.upper()}.png"
        if not upper.exists():  # a case-sensitive disk: an entry's name is lowercase hex
            upper.write_bytes(b"png")
            self.assertIsNone(job._library_file(str(upper), library))
        original = job.MAX_BACKGROUND_BYTES
        try:
            job.MAX_BACKGROUND_BYTES = 2
            self.assertIsNone(job._library_file(str(kept), library), "larger than the library takes")
        finally:
            job.MAX_BACKGROUND_BYTES = original
        index = library / "index.json"
        listing = index.read_bytes()
        for broken in (b"", b"{", b"[]", b'{"items": 3}', b'{"items": [3, null, {"id": 1}]}', b'{"schema_version": 1, "items": []}'):
            with self.subTest(index=broken):
                index.write_bytes(broken)
                self.assertIsNone(job._library_file(str(kept), library))
        index.unlink()
        self.assertIsNone(job._library_file(str(kept), library), "no index")
        index.write_bytes(listing)
        self.assertEqual(job._library_file(str(kept), library), kept)
        kept.write_bytes(b"swapped")
        self.assertIsNone(job._library_file(str(kept), library), "its bytes are read on every call")

    def test_the_worker_is_given_the_library_on_the_command_line(self):
        from unittest import mock
        from engine.dubflow.product import __main__ as worker
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        symlink(self.root / "data", self.root / "alias", self)
        through = self.root / "alias" / "backgrounds"
        # the spec cannot name its own library: only the command line does
        spec = self.write_spec(background=str(through / kept.name), library_dir=str(through), library=str(through))
        argv = ["--spec", str(spec), "--output-dir", str(self.root / "out"), "--app-root", str(self.root), "--model-root", str(self.root),
                "--ffmpeg", "ffmpeg", "--ffprobe", "ffprobe"]
        with mock.patch("builtins.print"):
            self.assertEqual(worker.main(argv), 1)
        status = json.loads((self.root / "out" / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["message"], f"Ảnh nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: {kept.name}")
        loaded = []
        real = worker.load_spec

        def load(*args):
            loaded.append(real(*args))
            raise job.ProductError("dừng ở đây")
        with mock.patch.object(worker, "load_spec", load), mock.patch("builtins.print"):
            self.assertEqual(worker.main(argv + ["--library-dir", str(through)]), 1)
        self.assertEqual([spec.background for spec in loaded], [through / kept.name], "the library named on the command line")


class ReadOnlyRenderTests(SpecFixture):
    """A whole Bán hàng render; kept apart from the rule's own cases so those can run alone (the Windows lane)."""

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

    def transcript(self, value: str):
        return program._source({"video": str(self.clip), "transcript": value}, "Mục 1, nguồn 1").transcript

    def assert_transcript_refused(self, value: str, message: str) -> None:
        with self.assertRaises(program.CompilationError) as caught:
            self.transcript(value)
        self.assertEqual(caught.exception.message, message)

    def test_a_named_transcript_is_a_real_file_or_a_hard_link(self):
        srt = self.root / "real" / "t.srt"
        srt.parent.mkdir()
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
        self.assertEqual(self.transcript(str(srt)), srt)
        os.link(srt, self.root / "hard.srt")
        self.assertEqual(self.transcript(str(self.root / "hard.srt")), self.root / "hard.srt")
        # what was refused before keeps its text
        for value in ("t.srt", str(self.root / "missing.srt"), str(self.clip)):
            self.assert_transcript_refused(value, f"Không mở được phụ đề nguồn: {Path(value).name}")

    def test_a_named_transcript_follows_the_same_rule(self):
        # Issue #816 review: the transcript a spec names was read by `_file`, which follows links and `..`
        srt = self.root / "real" / "t.srt"
        srt.parent.mkdir()
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
        self.assert_transcript_refused(os.path.join(str(self.root), "real", "..", "real", "t.srt"),
                                       "Đường dẫn phụ đề nguồn không hợp lệ: t.srt")
        symlink(srt, self.root / "link.srt", self)
        self.assert_transcript_refused(str(self.root / "link.srt"),
                                       "Phụ đề nguồn phải là tệp thật trên máy, không phải lối tắt: link.srt")
        symlink(srt.parent, self.root / "alias", self)
        self.assert_transcript_refused(str(self.root / "alias" / "t.srt"),
                                       "Phụ đề nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: t.srt")

    def program(self, music: str):
        items = [{"title": f"Mục {number}", "sources": [{"video": str(self.clip)}]} for number in (1, 2)]
        return program.load({"schema_version": 2, "title": "Tổng hợp", "layout": "toan-man-hinh", "voice_id": "vi-test",
                             "music": music, "items": items})

    def test_music_follows_the_same_rule(self):
        # Issue #816 review: a re-render's music was read by `_file`, which follows links and `..`
        music = self.root / "real" / "a.mp3"
        music.parent.mkdir()
        music.write_bytes(b"mp3")
        self.assertEqual(self.program(str(music)).music, music, "the real file at its real place")
        with self.assertRaises(program.CompilationError) as caught:
            self.program(os.path.join(str(self.root), "real", "..", "real", "a.mp3"))
        self.assertEqual(caught.exception.message, "Đường dẫn nhạc nền không hợp lệ: a.mp3")
        symlink(music, self.root / "music-link.mp3", self)
        with self.assertRaises(program.CompilationError) as caught:
            self.program(str(self.root / "music-link.mp3"))
        self.assertEqual(caught.exception.message, "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: music-link.mp3")
        symlink(music.parent, self.root / "alias", self)
        with self.assertRaises(program.CompilationError) as caught:
            self.program(str(self.root / "alias" / "a.mp3"))
        self.assertEqual(caught.exception.message,
                         "Nhạc nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: a.mp3")


class LegacyMusicFixture(Fixture):
    """The music the user picks for Tổng hợp from finished videos (spec v1) and for Đóng gói."""

    def setUp(self) -> None:
        super().setUp()
        self.music = self.root / "real" / "song.mp3"
        self.music.parent.mkdir()
        self.music.write_bytes(b"music")

    def compilation(self, music):
        items = []
        for number in (1, 2):
            video = self.root / "DubFlow Output" / f"job-{number}" / "final_vi.mp4"
            video.parent.mkdir(parents=True, exist_ok=True)
            video.write_bytes(b"video")
            items.append({"video": str(video), "title": f"Clip {number}", "lead": "Nhận xét này.", "score": 8, "pros": [], "cons": []})
        value = {"schema_version": 1, "title": "Tổng hợp thử", "layout": "bang-thong-tin", "voice_id": "vi-test", "speed": 1.1,
                 "music": music, "music_volume": 0.2, "intro": "Xin chào.", "outro": "", "items": items}
        (self.root / "compilation.json").write_text(json.dumps(value), encoding="utf-8")
        return compilation_job.load_spec(self.root / "compilation.json").music

    def package(self, music):
        video = self.root / "final_vi.mp4"
        video.write_bytes(b"video")
        value = {"schema_version": 1, "video": str(video), "intro": "Xin chào.", "outro": "", "voice_id": "vi-test", "speed": 1.0,
                 "music": music, "music_volume": 0.2, "background": "keep"}
        (self.root / "package.json").write_text(json.dumps(value), encoding="utf-8")
        return package_job.load_spec(self.root / "package.json").music

    def pages(self):
        return (("Tổng hợp v1", self.compilation), ("Đóng gói", self.package))

    def assert_music_refused(self, value: str, message: str) -> None:
        for page, load in self.pages():
            with self.subTest(page=page):
                with self.assertRaises(job.ProductError) as caught:
                    load(value)
                self.assertEqual(caught.exception.message, message)


class LegacyMusicTests(LegacyMusicFixture):
    def test_real_and_hard_linked_music_is_kept(self):
        hard = self.root / "hard.MP3"
        os.link(self.music, hard)
        for page, load in self.pages():
            with self.subTest(page=page):
                self.assertEqual(load(str(self.music)), self.music, "a real file anywhere is the user's file")
                self.assertEqual(load(str(hard)), hard, "a hard link is the file itself")
                self.assertIsNone(load(None))

    def test_traversal_relative_and_missing_music_is_refused(self):
        traversal = os.path.join(str(self.root), "real", "..", "real", "song.mp3")
        self.assertTrue(os.path.isfile(traversal), "the path names a file that exists: only the `..` refuses it")
        self.assert_music_refused(traversal, "Đường dẫn nhạc nền không hợp lệ: song.mp3")
        # what these pages' worker already refused keeps its text
        self.assert_music_refused("song.mp3", "Không mở được nhạc nền: song.mp3")
        self.assert_music_refused(str(self.root / "real" / "missing.mp3"), "Không mở được nhạc nền: missing.mp3")
        (self.root / "song.txt").write_bytes(b"music")
        self.assert_music_refused(str(self.root / "song.txt"), "Không mở được nhạc nền: song.txt")

    def test_linked_music_is_refused(self):
        symlink(self.music, self.root / "link.mp3", self)
        self.assertTrue((self.root / "link.mp3").is_file())
        self.assert_music_refused(str(self.root / "link.mp3"), "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: link.mp3")
        self.assertEqual(self.music.read_bytes(), b"music")

    def test_music_below_a_linked_folder_is_refused(self):
        symlink(self.music.parent, self.root / "alias", self)
        self.assertTrue((self.root / "alias" / "song.mp3").is_file(), "reachable through the link")
        self.assert_music_refused(str(self.root / "alias" / "song.mp3"),
                                  "Nhạc nền nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: song.mp3")


class JunctionTests(LegacyMusicFixture, SpecFixture):
    """The rule against a real directory junction, which only Windows has: the lane `PR Desktop Editor Windows` runs
    these and fails when one is skipped. Elsewhere they skip; the symlink cases above cover the same code."""

    BELOW = "nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật"

    def setUp(self) -> None:
        super().setUp()
        (self.music.parent / "deeper").mkdir()
        for name in ("b.mp4", "deeper/c.mp4"):
            (self.music.parent / name).write_bytes(b"clip")
        self.alias = self.root / "alias"
        junction(self.music.parent, self.alias, self)

    def test_a_junction_is_a_redirect(self):
        info = os.lstat(self.alias)
        self.assertEqual(info.st_reparse_tag, stat.IO_REPARSE_TAG_MOUNT_POINT, "a junction, not a symlink")
        self.assertTrue(job._redirect(info))
        self.assertFalse(job._redirect(os.lstat(self.music.parent)), "the real folder")
        self.assertFalse(job._redirect(os.lstat(self.music)), "a real file")

    def test_a_source_below_a_junction_is_refused(self):
        for name in ("b.mp4", "deeper/c.mp4"):
            self.assertTrue((self.alias / name).is_file(), "reachable through the junction")
            self.refused(str(self.alias / name), f"Video nguồn {self.BELOW}: {Path(name).name}")
            self.assertEqual(job._plain_file(str(self.music.parent / name), CLIPS, "video nguồn"), self.music.parent / name,
                             "the same file at its real place")
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(self.write_spec(visuals=[str(self.alias / "b.mp4")]))
        self.assertEqual(caught.exception.message, f"Video nguồn {self.BELOW}: b.mp4")
        with self.assertRaises(program.CompilationError) as caught:
            program._source({"video": str(self.alias / "b.mp4")}, "Mục 1, nguồn 1")
        self.assertEqual(caught.exception.message, f"Video nguồn {self.BELOW}: b.mp4")
        (self.music.parent / "t.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")
        with self.assertRaises(program.CompilationError) as caught:
            program._source({"video": str(self.clip), "transcript": str(self.alias / "t.srt")}, "Mục 1, nguồn 1")
        self.assertEqual(caught.exception.message, f"Phụ đề nguồn {self.BELOW}: t.srt")

    def test_music_below_a_junction_is_refused(self):
        through = str(self.alias / "song.mp3")
        self.assert_music_refused(through, f"Nhạc nền {self.BELOW}: song.mp3")
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(self.write_spec(music=through))
        self.assertEqual(caught.exception.message, f"Nhạc nền {self.BELOW}: song.mp3")
        items = [{"title": f"Mục {number}", "sources": [{"video": str(self.clip)}]} for number in (1, 2)]
        with self.assertRaises(program.CompilationError) as caught:
            program.load({"schema_version": 2, "title": "Tổng hợp", "layout": "toan-man-hinh", "voice_id": "vi-test",
                          "music": through, "items": items})
        self.assertEqual(caught.exception.message, f"Nhạc nền {self.BELOW}: song.mp3")

    def test_a_folder_swapped_for_a_junction_is_refused(self):
        moved = self.root / "moved"
        spec = self.write_spec(visuals=[str(moved / "b.mp4")])
        self.music.parent.rename(moved)
        self.assertEqual(job.load_spec(spec).visuals, (moved / "b.mp4",), "a real folder when the desktop checked it")
        moved.rename(self.root / "elsewhere")
        junction(self.root / "elsewhere", moved, self)
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(spec)
        self.assertEqual(caught.exception.message, f"Video nguồn {self.BELOW}: b.mp4")

    def test_a_library_background_below_a_junctioned_data_folder_is_kept(self):
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        junction(self.root / "data", self.root / "data-alias", self)
        through = self.root / "data-alias" / "backgrounds"
        self.assertEqual(job.load_spec(self.write_spec(background=str(through / kept.name)), through).background, through / kept.name,
                         "the data folder may be reached through a junction")
        junction(library, self.root / "library-alias", self)
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(self.write_spec(background=str(self.root / "library-alias" / kept.name)), self.root / "library-alias")
        self.assertEqual(caught.exception.message, f"Ảnh nền {self.BELOW}: {kept.name}", "the library folder itself may not")

    def test_only_the_apps_proven_copy_may_sit_below_a_junction(self):
        library = self.root / "data" / "backgrounds"
        kept = library_entry(library, b"png")
        placed = library_entry(library, b"placed", listed=False)
        changed = library_entry(library, b"listed")
        changed.write_bytes(b"other bytes")
        named = self.root / "data" / kept.name
        named.write_bytes(b"png")
        (self.root / "data" / "own.png").write_bytes(b"png")
        junction(self.root / "data", self.root / "data-alias", self)
        alias = self.root / "data-alias"
        through = alias / "backgrounds"
        with self.assertRaises(job.ProductError) as caught:
            job.load_spec(self.write_spec(background=str(through / kept.name)))
        self.assertEqual(caught.exception.message, f"Ảnh nền {self.BELOW}: {kept.name}", "no library named: the user's rule")
        for path in (alias / "own.png", alias / named.name, through / placed.name, through / changed.name):
            with self.subTest(path.name), self.assertRaises(job.ProductError) as caught:
                job.load_spec(self.write_spec(background=str(path)), through)
            self.assertEqual(caught.exception.message, f"Ảnh nền {self.BELOW}: {path.name}")
        for path in (self.root / "data" / "own.png", named, placed, changed):
            self.assertEqual(job.load_spec(self.write_spec(background=str(path)), library).background, path, "at its real place")

if __name__ == "__main__":
    unittest.main()

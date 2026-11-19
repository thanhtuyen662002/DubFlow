"""Release notes selection (#969): which notes a build carries into the update manifest and the GitHub Release."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packaging.release import notes

ROOT = Path(__file__).resolve().parents[2]


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / notes.NOTES_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # exact bytes: no newline translation on Windows
    return root


class ReleaseNotesTests(unittest.TestCase):
    def test_a_candidate_carries_the_notes_of_the_release_it_prepares(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _tree(Path(tmp), {"1.2.0.md": "DubFlow 1.2.0\r\n\r\n- Bản nháp tự lưu\n"})
            for version in ("1.2.0", "1.2.0-rc1", "1.2.0-rc12", "v1.2.0-rc1"):
                self.assertEqual(notes.notes_for(version, root), "DubFlow 1.2.0\n\n- Bản nháp tự lưu", version)

    def test_a_version_without_notes_keeps_the_plain_line(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _tree(Path(tmp), {"1.2.0.md": "x"})
            self.assertEqual(notes.notes_for("1.1.96-rc32", root), "DubFlow 1.1.96-rc32")
            self.assertEqual(notes.notes_for("1.2.1", root), "DubFlow 1.2.1")

    def test_oversize_or_empty_notes_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _tree(Path(tmp), {"1.2.0.md": "a" * (notes.MAX_CHARS + 1), "1.3.0.md": " \n"})
            with self.assertRaises(notes.NotesError):
                notes.notes_for("1.2.0", root)
            with self.assertRaises(notes.NotesError):
                notes.notes_for("1.3.0-rc1", root)
            _tree(Path(tmp), {"1.2.0.md": "a" * notes.MAX_CHARS})
            self.assertEqual(len(notes.notes_for("1.2.0", root)), notes.MAX_CHARS)

    def test_unsafe_versions_are_refused(self) -> None:
        for version in ("", "1", "1.2.0/../x", "../1.2.0", "1.2.0-", "1.2.0-rc1 ", "1.2.0-a/b", "1.2.0-a\\b", "vv1.2.0"):
            with self.assertRaises(notes.NotesError, msg=version):
                notes.release_of(version)

    def test_cli_writes_the_chosen_notes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _tree(Path(tmp), {"1.2.0.md": "Ghi chú"})
            out = Path(tmp) / "dist" / "DubFlow-1.2.0-rc1-release-notes.md"
            self.assertEqual(notes.main(["--version", "1.2.0-rc1", "--source-root", str(root), "--out", str(out)]), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "Ghi chú\n")
            self.assertEqual(notes.main(["--version", "../x", "--source-root", str(root), "--out", str(out)]), 2)

    def test_the_shipped_1_2_0_notes_fit_and_say_what_is_not_verified(self) -> None:
        text = notes.notes_for("1.2.0", ROOT)
        self.assertTrue(text.startswith("DubFlow 1.2.0"))
        self.assertLessEqual(len(text), notes.MAX_CHARS)
        self.assertIn("chưa được kiểm chứng", text, "the notes must say real AI quality is not verified")


if __name__ == "__main__":
    unittest.main()

"""Nhạc and SFX: the accompaniment split into a pitched part and a short/broadband part that add back up."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import wave

import numpy as np

from engine.dubflow.separation import outdir, split

RATE = 16000


def write(path: Path, data: np.ndarray) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(data.shape[1])
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(np.clip(np.round(data * 32767), -32768, 32767).astype("<i2").tobytes())


def read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as reader:
        return np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(np.int64).reshape(-1, reader.getnchannels())


def corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b))


def link_dir(target: Path, link: Path) -> None:
    """A folder link: a junction on Windows (no privilege needed), a symlink elsewhere. Fails loudly."""
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, stdout=subprocess.DEVNULL)
    else:
        os.symlink(target, link, target_is_directory=True)
    assert os.path.lexists(link) and os.path.realpath(link) != os.path.abspath(link), "the link was not made"


def unlink_link(link: Path) -> None:
    """Remove a folder link without touching its target (a junction is removed as a folder)."""
    try:
        os.rmdir(link) if sys.platform == "win32" else os.unlink(link)
    except OSError:
        os.unlink(link)


def snapshot(folder: Path) -> dict:
    """Every file below ``folder``: bytes hash and mtime."""
    return {str(path.relative_to(folder)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted(folder.rglob("*")) if path.is_file()}


class MusicSplitTest(unittest.TestCase):
    def test_a_tone_is_music_clicks_are_sfx_and_they_add_back_up(self):
        count = RATE * 45  # three blocks: the block seams must not show
        t = np.arange(count) / RATE
        tone = 0.2 * np.sin(2 * np.pi * 440 * t)
        clicks = np.zeros(count)
        for start in range(0, count, RATE // 2):
            clicks[start:start + 200] += 0.3 * np.random.RandomState(start).randn(200) * np.exp(-np.arange(200) / 40)
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.stack([tone + clicks, tone + clicks], axis=1))
            source = folder / "source.wav"
            voice = 0.1 * np.sin(2 * np.pi * 180 * t)
            write(source, np.stack([tone + clicks + voice, tone + clicks + voice], axis=1))
            seen = []
            document = split.split(background, folder, source=source, progress=seen.append)
            spoken = read(folder / "voice.wav")
            music, sfx, whole = read(folder / "music.wav"), read(folder / "sfx.wav"), read(background)
            saved = json.loads((folder / split.SPLIT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(music.shape, whole.shape)
        self.assertLessEqual(int(np.abs(music + sfx - whole).max()), 2)
        self.assertGreater(corr(music[:, 0] / 32768, tone), 0.99)
        self.assertGreater(corr(sfx[:, 0] / 32768, clicks), 0.95)
        self.assertEqual(seen[-1], 1.0)
        self.assertGreater(corr(spoken[:, 0] / 32768, voice), 0.99)
        self.assertEqual(saved, document)
        self.assertEqual(saved["recipe"], split.RECIPE)

    def test_a_short_mono_file_and_a_bad_format(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            short = folder / "short.wav"
            write(short, np.full((100, 1), 0.1))
            split.split(short, folder)
            self.assertEqual(read(folder / "music.wav").shape, (100, 1))
            bad = folder / "bad.wav"
            with wave.open(str(bad), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(1)
                writer.setframerate(RATE)
                writer.writeframes(b"\x80" * 100)
            with self.assertRaises(split.SplitError):
                split.split(bad, folder)

    def test_cancel_leaves_no_parts(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.zeros((RATE, 2)))
            with self.assertRaises(split.SplitError):
                split.split(background, folder / "out", cancelled=lambda: True)
            self.assertEqual(sorted(path.name for path in (folder / "out").iterdir()), [])

    def test_a_linked_marker_is_replaced_not_followed(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            out = folder / "out"
            out.mkdir()
            outside = folder / "outside.json"
            outside.write_text("keep me", encoding="utf-8")
            before = (outside.read_bytes(), outside.stat().st_mtime_ns)
            os.symlink(outside, out / split.SPLIT_FILE)
            background = folder / "background.wav"
            write(background, np.full((RATE, 2), 0.1))
            split.split(background, out)
            self.assertEqual((outside.read_bytes(), outside.stat().st_mtime_ns), before)
            marker = out / split.SPLIT_FILE
            self.assertFalse(marker.is_symlink())
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["recipe"], split.RECIPE)

    def test_an_interrupted_retry_leaves_no_marker_for_a_mixed_pair(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.full((RATE, 2), 0.1))
            out = folder / "out"
            split.split(background, out)
            # An old valid marker, one part missing; the retry stops after replacing the first part.
            (out / "sfx.wav").unlink()
            self.assertTrue((out / split.SPLIT_FILE).is_file())
            real = outdir._Temp.publish
            calls = []

            def publish(temp, target):
                calls.append(target)
                if len(calls) == 2:
                    raise OSError("interrupted")
                return real(temp, target)
            with mock.patch.object(outdir._Temp, "publish", publish), self.assertRaises(OSError):
                split.split(background, out)
            self.assertEqual(calls[0], "music.wav")
            self.assertFalse((out / split.SPLIT_FILE).exists(), "no marker blesses the new music beside a missing/old SFX")
            self.assertEqual([path.name for path in out.iterdir() if path.name.startswith(".")], [], "no temporary files left")


    def test_a_linked_output_folder_or_parent_is_refused_before_anything_changes(self):
        """Out folder linked (symlink, or junction on Windows), or the draft folder above it linked:
        the split stops before removing or writing anything, and the outside marker stays byte-for-byte."""
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.full((RATE, 2), 0.1))
            outside = folder / "outside"
            (outside / "stems").mkdir(parents=True)
            for name in (split.SPLIT_FILE, "music.wav", "sfx.wav", "voice.wav"):
                (outside / "stems" / name).write_text(f"keep {name}", encoding="utf-8")
            before = snapshot(outside)
            drafts = folder / "drafts"
            drafts.mkdir()
            # 1) The out folder itself is a link; 2) the draft folder holding it is.
            link_dir(outside / "stems", drafts / "stems-link")
            link_dir(outside, drafts / "work-link")
            for out in (drafts / "stems-link", drafts / "work-link" / "stems"):
                with self.subTest(out=out.name):
                    with self.assertRaises(split.SplitError):
                        split.split(background, out, source=background)
                    self.assertEqual(split.main(["--background", str(background), "--out-dir", str(out)]), 1)
                    self.assertEqual(snapshot(outside), before, "nothing outside is removed, replaced or added")

    def test_a_missing_out_folder_is_made_only_under_plain_folders(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.full((RATE, 2), 0.1))
            # A plain draft folder: the out folder is made on its own.
            (folder / "work").mkdir()
            split.split(background, folder / "work" / "stems")
            self.assertTrue((folder / "work" / "stems" / split.SPLIT_FILE).is_file())
            # A missing draft folder is never made (no parents), and nothing appears.
            with self.assertRaises(split.SplitError):
                split.split(background, folder / "missing" / "stems")
            self.assertFalse((folder / "missing").exists())


    def _race(self, *, when: str, level: str):
        """Run a split while the stems folder (``level="stems"``) or the draft folder above it
        (``level="work"``) is renamed and its path swapped for a link (a junction on Windows) to an
        outside folder seeded with every name the split writes, including its temporary files (made
        predictable here). ``when``: right after the folder is checked and opened, before the first
        operation; mid-run with the parts' temporary files open; or after each publication."""
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            background = folder / "background.wav"
            write(background, np.full((RATE, 2), 0.1))
            (folder / "drafts" / "work" / "stems").mkdir(parents=True)
            out = folder / "drafts" / "work" / "stems"
            moved = folder / "drafts" / f"{level}-moved"
            swapped = out if level == "stems" else out.parent
            outside = folder / "outside"
            target = outside / "stems" if level == "work" else outside
            target.mkdir(parents=True)
            names = [split.SPLIT_FILE, "music.wav", "sfx.wav", "voice.wav", "split-status.json",
                     ".music-x.wav", ".sfx-x.wav", ".voice-x.wav", ".split-x.json", ".status-x.json"]
            for name in names:
                (target / name).write_text(f"keep {name}", encoding="utf-8")
            before = snapshot(outside)
            state = {"swapped": None, "events": 0}

            def swap():
                if state["swapped"] is not None:
                    return
                try:
                    os.rename(swapped, moved)
                except PermissionError:
                    # Windows may refuse to rename a folder while files in it are open: no swap, nothing to follow.
                    self.assertEqual(sys.platform, "win32", "only Windows refuses the rename")
                    state["swapped"] = False
                    return
                link_dir(outside, swapped)
                state["swapped"] = True

            real_open, real_unlink, real_publish = outdir.OutDir.open.__func__, outdir.OutDir.unlink, outdir._Temp.publish

            def opened(cls, path, **kwargs):
                handle = real_open(cls, path, **kwargs)
                if when == "open":
                    swap()  # between the check (the open) and the first operation (the old marker's removal)
                return handle

            def published(temp, target_name):
                real_publish(temp, target_name)
                state["events"] += 1
                if when.startswith("publish-") and state["events"] == int(when.split("-")[1]):
                    swap()

            progress = (lambda _fraction: swap()) if when == "mid-run" else None
            with mock.patch.object(outdir, "temp_name", lambda prefix, suffix: f"{prefix}x{suffix}"), \
                    mock.patch.object(outdir.OutDir, "open", classmethod(opened)), \
                    mock.patch.object(outdir._Temp, "publish", published):
                document = split.split(background, out, source=background, progress=progress)
            self.assertEqual(document["recipe"], split.RECIPE)
            if sys.platform != "win32":
                self.assertTrue(state["swapped"], "the swap happened")
            self.assertEqual(snapshot(outside), before, "nothing outside is deleted, replaced, added or changed")
            if state["swapped"]:
                real_out = moved / "stems" if level == "work" else moved
                self.assertEqual(sorted(p.name for p in real_out.iterdir()), sorted([split.SPLIT_FILE, "music.wav", "sfx.wav", "voice.wav"]),
                                 "every write landed in the folder that was opened")
                unlink_link(swapped)
            print(f"race when={when} level={level} swapped={state['swapped']}")

    def test_a_swap_between_check_and_operation_is_never_followed(self):
        for when in ("open", "mid-run", "publish-1", "publish-2", "publish-3"):
            for level in ("stems", "work"):
                with self.subTest(when=when, level=level):
                    self._race(when=when, level=level)

    def test_repeated_status_writes_never_follow_a_swapped_folder(self):
        from engine.dubflow.separation.__main__ import Status

        with TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "drafts" / "work" / "stems").mkdir(parents=True)
            out = folder / "drafts" / "work" / "stems"
            outside = folder / "outside"
            outside.mkdir()
            for name in ("split-status.json", ".status-x.json"):
                (outside / name).write_text("keep", encoding="utf-8")
            before = snapshot(outside)
            with mock.patch.object(outdir, "temp_name", lambda prefix, suffix: f"{prefix}x{suffix}"):
                with split.open_out_dir(out) as held:
                    status = Status(out / "split-status.json", folder=held)
                    status.write(progress=1)
                    os.rename(out, folder / "drafts" / "work" / "stems-moved")
                    link_dir(outside, out)
                    for progress in (20, 40, 60):
                        status.write(progress=progress)
                    # Without a held folder each write opens the path again and refuses the link.
                    Status(out / "split-status.json").write(progress=99)
            self.assertEqual(snapshot(outside), before)
            moved = json.loads((folder / "drafts" / "work" / "stems-moved" / "split-status.json").read_text(encoding="utf-8"))
            self.assertEqual(moved["progress"], 60, "the held folder kept getting the status")
            unlink_link(out)

if __name__ == "__main__":
    unittest.main()

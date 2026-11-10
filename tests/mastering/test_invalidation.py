"""Caches and saved plans follow the content, the script, the settings and the recipe (Phase 2 closure)."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering.planners import product as planner

BALANCED = m.Options("balanced")


def same_size_same_mtime(path: Path, data: bytes) -> None:
    """Replace the bytes but keep the size and the mtime, as copy tools and sync clients can."""
    stat = path.stat()
    assert len(data) == stat.st_size
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    m._FINGERPRINTS.clear()  # a new process


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        m._FINGERPRINTS.clear()

    def tearDown(self):
        self.folder.cleanup()

    def test_a_small_file_changed_with_size_and_mtime_kept_is_a_new_identity(self):
        clip = self.root / "a.mp4"
        clip.write_bytes(b"A" * 5000)
        before = m.file_identity(clip, scene.RECIPE)
        same_size_same_mtime(clip, b"A" * 2500 + b"B" + b"A" * 2499)
        self.assertNotEqual(m.file_identity(clip, scene.RECIPE), before)

    def test_a_large_file_is_sampled_across_its_whole_length(self):
        clip = self.root / "big.mp4"
        size = m.FULL_HASH_LIMIT + 5 * 1024 * 1024
        with clip.open("wb") as out:
            out.truncate(size)
        before = m.content_fingerprint(clip)
        for where in (0, size // 2, size - 1):  # header, middle samples, trailing index
            with self.subTest(where=where):
                stat = clip.stat()
                with clip.open("r+b") as out:
                    block = m.FINGERPRINT_BLOCK
                    last = size - block
                    # Put the byte inside the sampled block nearest to ``where``.
                    index = round(where * (m.FINGERPRINT_BLOCKS - 1) / max(1, size - 1))
                    out.seek(last * index // (m.FINGERPRINT_BLOCKS - 1) + 10)
                    out.write(b"\x01")
                os.utime(clip, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                m._FINGERPRINTS.clear()
                after = m.content_fingerprint(clip)
                self.assertNotEqual(after, before)
                before = after

    def test_the_large_file_read_is_bounded(self):
        clip = self.root / "big.mp4"
        with clip.open("wb") as out:
            out.truncate(m.FULL_HASH_LIMIT * 4)
        reads = []
        real_open = Path.open

        def counting(self, *args, **kwargs):
            handle = real_open(self, *args, **kwargs)
            original = handle.read

            def read(n=-1):
                data = original(n)
                reads.append(len(data))
                return data
            handle.read = read
            return handle
        with mock.patch.object(Path, "open", counting):
            m.content_fingerprint(clip)
        self.assertLessEqual(sum(reads), m.FINGERPRINT_BLOCKS * m.FINGERPRINT_BLOCK)

    def test_identity_does_not_depend_on_the_name_or_the_mtime(self):
        a, b = self.root / "a.mp4", self.root / "b.mp4"
        a.write_bytes(b"same")
        b.write_bytes(b"same")
        os.utime(b, (1, 1))
        self.assertEqual(m.file_identity(a, "r"), m.file_identity(b, "r"))
        self.assertNotEqual(m.file_identity(a, "r"), m.file_identity(a, "r2"))  # a new analysis recipe

    def test_the_analysis_cache_is_named_by_content_so_a_reorder_reuses_it(self):
        a, b = self.root / "a.mp4", self.root / "b.mp4"
        a.write_bytes(b"one")
        b.write_bytes(b"two")
        folder = self.root / "cache"
        self.assertNotEqual(scene.cache_path(folder, a), scene.cache_path(folder, b))
        before = scene.cache_path(folder, a)
        moved = self.root / "renamed.mp4"
        a.rename(moved)
        m._FINGERPRINTS.clear()
        self.assertEqual(scene.cache_path(folder, moved), before)

    def test_a_changed_clip_is_analysed_again(self):
        clip = self.root / "a.mp4"
        clip.write_bytes(b"A" * 100)
        info = scene.StreamInfo(1000, 16, 16, 30, False)
        cache = self.root / "c.json"
        calls = []

        def run(argv, cwd, cancelled):
            calls.append(argv)
            (cwd / f"{cache.stem}.scdet.txt").write_text("")
            (cwd / f"{cache.stem}.thumbs.gray").write_bytes(b"\x00" * (32 * 18 * 5))
        scene.analyse(clip, info, cache, ffmpeg=Path("ffmpeg"), run=run)
        scene.analyse(clip, info, cache, ffmpeg=Path("ffmpeg"), run=run)
        self.assertEqual(len(calls), 1)  # unchanged: cached
        same_size_same_mtime(clip, b"B" * 100)
        scene.analyse(clip, info, cache, ffmpeg=Path("ffmpeg"), run=run)
        self.assertEqual(len(calls), 2)


class PlanReuseTests(unittest.TestCase):
    """A saved product plan is reused only for the same files, script, settings and planner."""

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.clip = self.root / "a.mp4"
        self.clip.write_bytes(b"clip")
        m._FINGERPRINTS.clear()
        found = scene.Analysis(10_000, (4000,), (scene.Shot(0, 4000, 50), scene.Shot(4000, 10_000, 50)), (), True, "id")
        self.visuals = [planner.Visual(self.clip, 10_000, 1080, 1920, found)]
        self.spoken = [("Câu một.", 2000), ("Câu hai.", 3000)]
        self.wanted = planner.inputs([self.clip], 6000, BALANCED, spoken=self.spoken, template="ban-tin")
        self.path = self.root / "plan.json"
        m.save(self.path, planner.plan(self.visuals, 6000, BALANCED, inputs=self.wanted))

    def tearDown(self):
        self.folder.cleanup()

    def load(self, inputs, options=BALANCED, version=planner.VERSION):
        return m.load_matching(self.path, workflow=planner.WORKFLOW, options=options, planner_version=version, inputs=inputs)

    def test_same_inputs_reuse(self):
        self.assertIsNotNone(self.load(planner.inputs([self.clip], 6000, BALANCED, spoken=self.spoken, template="ban-tin")))

    def test_every_change_plans_again(self):
        changes = {
            "script": planner.inputs([self.clip], 6000, BALANCED, spoken=[("Câu một.", 2000), ("Câu khác.", 3000)], template="ban-tin"),
            "voice tempo": planner.inputs([self.clip], 6000, BALANCED, spoken=[("Câu một.", 1800), ("Câu hai.", 3000)], template="ban-tin"),
            "length": planner.inputs([self.clip], 6100, BALANCED, spoken=self.spoken, template="ban-tin"),
            "template": planner.inputs([self.clip], 6000, BALANCED, spoken=self.spoken, template="toan-man-hinh"),
            "style": planner.inputs([self.clip], 6000, m.Options("balanced", "cinematic"), spoken=self.spoken, template="ban-tin"),
        }
        for name, inputs in changes.items():
            with self.subTest(name=name):
                self.assertIsNone(self.load(inputs))
        self.assertIsNone(self.load(self.wanted, options=m.Options("dynamic")))
        self.assertIsNone(self.load(self.wanted, version=planner.VERSION + "-next"))
        same_size_same_mtime(self.clip, b"CLIP")
        self.assertIsNone(self.load(planner.inputs([self.clip], 6000, BALANCED, spoken=self.spoken, template="ban-tin")))

    def test_a_new_analysis_recipe_plans_again(self):
        with mock.patch.object(scene, "RECIPE", scene.RECIPE + "+next"):
            self.assertIsNone(self.load(planner.inputs([self.clip], 6000, BALANCED, spoken=self.spoken, template="ban-tin")))


if __name__ == "__main__":
    unittest.main()

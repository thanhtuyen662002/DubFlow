"""Bán hàng with the editorial planner: each sentence illustrated by matching footage (ADR-0054)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import align
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering.planners import product as planner
from engine.dubflow.product import job, render

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


def candidate(cid, source, start, end):
    return shots.Candidate(cid, source, start, end, 0, 50, shots.Quality(120, 50, 300), random.getrandbits(64))


def product_world():
    pool = [candidate("v1c1", "v1", 0, 6000), candidate("v2c1", "v2", 0, 6000), candidate("v3c1", "v3", 0, 6000),
            candidate("v3c2", "v3", 6000, 12_000)]
    found = ev.gather(c.candidate_id for c in pool)
    notes = {"v1": "Toàn cảnh nồi chiên màu đen trên bàn bếp", "v2": "Cận cảnh lòng nồi chống dính",
             "v3": "Thao tác bấm nút chọn chế độ và cho gà vào nồi"}
    for c in pool:
        found[c.candidate_id].add(ev.Item("user", 1000, notes[c.source_id], tuple(ev.cue_labels(notes[c.source_id]))))
    return pool, found


class ProductPlanTests(unittest.TestCase):
    def test_plan_links_sentences_frames_products_and_flags_unknown_numbers(self):
        pool, found = product_world()
        found["v2c1"].product_box = ev.box_from({"x": 600, "y": 200, "w": 300, "h": 500})
        visuals = [planner.Visual(Path(f"v{i}.mp4"), 12_000, 1920, 1080) for i in (1, 2, 3)]
        spoken = [("Nồi chiên không dầu cho cả nhà.", 2000), ("Lòng nồi chống dính, dung tích 9 lít.", 2500),
                  ("Chỉ cần bấm nút chọn chế độ.", 2200), ("Mua ngay hôm nay.", 1200)]
        total = 300 + sum(d for _, d in spoken) + 250 * 3 + 700
        candidates = {0: [pool[0]], 1: [pool[1]], 2: pool[2:]}
        made, log = planner.plan_editorial(visuals, spoken, total, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                           candidates=candidates, evidence=found, facts="Nồi chiên 6 lít 299.000đ", inputs={})
        self.assertEqual(made.duration, total)
        self.assertEqual(rules.check(made, rules.resolve("product_showcase", m.Options("balanced"))), [])
        refs = {s.speech_refs[0]: s.source_id for s in made.segments}
        self.assertEqual(refs["u2"], "v2")
        self.assertEqual(refs["u3"], "v3")
        closeup = next(s for s in made.segments if s.speech_refs == ("u2",))
        self.assertIsNotNone(closeup.framing)
        self.assertEqual(closeup.framing.center_x, 7500)
        self.assertTrue(closeup.needs_review)  # "9 lít" is not in the product information
        self.assertTrue(any("9" in w for w in made.warnings))
        piece = planner.pieces(made)[[s.segment_id for s in made.segments].index(closeup.segment_id)]
        self.assertEqual(piece.focus, (0.75, 0.45))
        self.assertEqual(log["mode"], "content")
        self.assertEqual(len(log["units"]), 4)
        json.dumps(log, ensure_ascii=False)
        m.Plan.from_dict(json.loads(made.to_json()))


SPOKEN = [("Nồi chiên không dầu cho cả nhà.", 2000), ("Lòng nồi chống dính rất dễ rửa.", 2500),
          ("Chỉ cần bấm nút chọn chế độ.", 2200)]


class SourceIdentityTests(unittest.TestCase):
    """The decision log's ``sources`` hold content fingerprints only: an unreadable source is unknown (P2-N5)."""

    def plan(self, paths, recipe, unreadable=()):
        pool, found = product_world()
        # the input identity first (the file's content and the analysis recipe), as the job computes it
        wanted = {f"v{i + 1}": m.file_identity(path, recipe) for i, path in enumerate(paths)}
        hidden = []
        for index in unreadable:  # gone after the inputs were computed, before the log is written
            moved = paths[index].with_suffix(".gone")
            paths[index].rename(moved)
            hidden.append((moved, paths[index]))
        try:
            visuals = [planner.Visual(path, 12_000, 1920, 1080) for path in paths]
            total = 300 + sum(d for _, d in SPOKEN) + 250 * 2 + 700
            _, log = planner.plan_editorial(visuals, SPOKEN, total, m.Options("balanced"), lead_ms=300, gap_ms=250,
                                            candidates={0: [pool[0]], 1: [pool[1]], 2: pool[2:]}, evidence=found,
                                            inputs=wanted)
        finally:
            for moved, back in hidden:
                moved.rename(back)
        return log

    def test_an_unreadable_source_after_a_new_recipe_never_voids_an_approval_as_source_changed(self):
        from engine.dubflow.mastering import review as rv
        with TemporaryDirectory() as folder:
            paths = [Path(folder) / f"v{i}.mp4" for i in (1, 2, 3)]
            for index, path in enumerate(paths):
                path.write_bytes(bytes([index + 1]) * 4096)
            first = self.plan(paths, "recipe-1")
            self.assertEqual(first["source_identity"], "content")
            self.assertEqual(first["sources"], {f"v{i + 1}": m.content_fingerprint(p) for i, p in enumerate(paths)})
            review = rv.Review()
            for unit in first["units"]:
                review.decide(first, unit["unit_id"], "approve")
            approved = {u: list(d.approved) for u, d in review.units.items() if d.approved is not None}
            self.assertEqual(len(approved), 3)
            # A release with another analysis recipe, and v1 unreadable when the log is written: v1 is unknown, never
            # its recipe-sensitive cache identity
            second = self.plan(paths, "recipe-2", unreadable=(0,))
            self.assertNotIn("v1", second["sources"])
            self.assertEqual(set(second["sources"].values()) - {m.content_fingerprint(p) for p in paths}, set())
            on_v1 = [u["unit_id"] for u in second["units"] if any(i.startswith("v1") for i in u["chosen"])]
            self.assertTrue(on_v1)
            self.assertTrue(all(rv.basis(u, second).endswith("-") for u in second["units"] if u["unit_id"] in on_v1))
            self.assertEqual(review.reconcile(second), [])
            self.assertEqual({u: list(d.approved) for u, d in review.units.items() if d.approved is not None}, approved)
            # readable again under the new recipe: the same content, still approved
            third = self.plan(paths, "recipe-2")
            self.assertEqual(review.reconcile(third), [])
            self.assertNotIn("source_changed", {c["cause"] for c in review.conflicts.values()})
            # the content itself changes: that is a reason, and it is said
            paths[0].write_bytes(b"other footage" * 400)
            fourth = self.plan(paths, "recipe-2")
            self.assertTrue(review.reconcile(fourth))
            self.assertEqual({c["cause"] for c in review.conflicts.values()}, {"source_changed"})
            self.assertEqual(sorted(review.conflicts), sorted(on_v1))


class PlanLogPairingTests(unittest.TestCase):
    """A saved plan is reused only with the log made with it (P2-N5, the log is written before the plan)."""

    def run_job(self, root, edits=None):
        value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test", "variants": ["x"],
                 "visuals": [str(root / f"v{i}.jpg") for i in (1, 2, 3)], "mastering": {"schema_version": 1, "level": "balanced"},
                 "editorial": {"notes": ["Toàn cảnh nồi chiên trên bàn", "Cận cảnh lòng nồi chống dính",
                                         "Thao tác bấm nút chọn chế độ"], **({"edits": edits} if edits else {})}}
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        spec = job.load_spec(spec_path)
        work = root / "work"
        work.mkdir(exist_ok=True)

        def no_ffmpeg(argv, cwd, cancelled):
            raise OSError("no FFmpeg here")  # a photo without measurements is still the seller's photo
        total = 300 + sum(d for _, d in SPOKEN) + 250 * 2 + 700
        return job._mastered(spec, [render.Visual(Path(v)) for v in value["visuals"]], total, work, 1, spoken=SPOKEN,
                             inspect=lambda path: None, measure=lambda path: (1080, 1080), ffmpeg=Path("ffmpeg"),
                             ffmpeg_runner=no_ffmpeg, cancelled=lambda: False), work

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        for index in (1, 2, 3):
            (self.root / f"v{index}.jpg").write_bytes(bytes([index]) * 2048)

    def tearDown(self):
        self.folder.cleanup()

    def saved(self, work):
        return (json.loads((work / "editorial-1.json").read_text(encoding="utf-8")),
                json.loads((work / "mastering_plan-1.json").read_text(encoding="utf-8")))

    def test_a_failed_plan_save_then_undo_never_pairs_the_old_plan_with_the_newer_log(self):
        from unittest import mock
        report, work = self.run_job(self.root)
        self.assertNotIn("fallback", report, report)
        self.assertFalse(report["reused"])
        old_log, old_plan = self.saved(work)
        self.assertEqual(self.run_job(self.root)[0]["reused"], True)  # the same inputs: plan and log reused
        # The user pins v1 to the second sentence: the new log is written, then saving the plan fails.
        pin = {"pin": {"u2": ["v1c1"]}}
        with mock.patch.object(m, "save", side_effect=OSError("disk full")):
            failed, _ = self.run_job(self.root, pin)
        self.assertIn("fallback", failed)
        newer_log, plan_left = self.saved(work)
        self.assertEqual(plan_left, old_plan)  # the old plan is still there
        self.assertNotEqual(newer_log["plan_inputs"], old_log["plan_inputs"])
        self.assertEqual(newer_log["edits"]["pin"], pin["pin"])
        # Undo: back to the first inputs. The old plan matches them, but the log beside it is the newer one.
        undone, _ = self.run_job(self.root)
        self.assertNotIn("fallback", undone, undone)
        self.assertFalse(undone["reused"])  # planned again, not the old plan with the pinned log
        log, plan = self.saved(work)
        self.assertEqual(log["plan_inputs"], old_log["plan_inputs"])
        self.assertEqual(log["edits"]["pin"], {})
        self.assertEqual(log["plan_id"], plan["plan_id"])
        self.assertEqual([u["chosen"] for u in log["units"]], [u["chosen"] for u in old_log["units"]])

    def test_an_older_log_without_plan_inputs_is_still_reused_with_its_plan(self):
        report, work = self.run_job(self.root)
        self.assertFalse(report["reused"])
        log, _ = self.saved(work)
        log.pop("plan_inputs")  # a log from before the key was written
        (work / "editorial-1.json").write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
        again, _ = self.run_job(self.root)
        self.assertNotIn("fallback", again, again)
        self.assertTrue(again["reused"])

    def test_an_unreadable_log_is_never_reused(self):
        report, work = self.run_job(self.root)
        (work / "editorial-1.json").write_text("{not json", encoding="utf-8")
        again, _ = self.run_job(self.root)
        self.assertNotIn("fallback", again, again)
        self.assertFalse(again["reused"])


class RenderFocusTests(unittest.TestCase):
    def test_focus_moves_the_crop_only(self):
        clip = render.Visual(Path("a.mp4"), 10_000)
        plain = render.ffmpeg_argv(Path("ffmpeg"), [render.Segment(clip, 0, 3000)], Path("n.wav"), None, 0.1, 3000, Path("o.mp4"))
        moved = render.ffmpeg_argv(Path("ffmpeg"), [render.Segment(clip, 0, 3000, (0.8, 0.3))], Path("n.wav"), None, 0.1, 3000,
                                   Path("o.mp4"))
        graph_plain = plain[plain.index("-filter_complex") + 1]
        graph_moved = moved[moved.index("-filter_complex") + 1]
        self.assertNotEqual(graph_plain, graph_moved)
        self.assertIn("iw*0.8000", graph_moved)
        self.assertEqual(graph_moved.count("scale="), graph_plain.count("scale="))  # no zoom, no stretch
        for word in ("rotate", "hflip", "vflip", "zoompan"):
            self.assertNotIn(word, graph_moved)


class FakeSpeaker:
    sample_rate = 48000

    def speak(self, text):
        return [0.1] * (48 * 300 * len(text.split()))

    def close(self):
        pass


@unittest.skipUnless(FFMPEG and FFPROBE, "needs FFmpeg")
class RealMediaJobTest(unittest.TestCase):
    """Three synthetic clips with the seller's notes: the video follows the script, not the input order."""

    def test_each_sentence_shows_the_clip_its_note_describes_and_edits_rerender(self):
        from engine.dubflow.mastering import analysis as scene
        with TemporaryDirectory() as folder:
            root = Path(folder)
            colours = {"tong-the.mp4": "testsrc2", "can-canh.mp4": "smptebars", "thao-tac.mp4": "rgbtestsrc"}
            paths = []
            for name, source in colours.items():
                path = root / name
                subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"{source}=s=1280x720:r=30:d=8", "-c:v", "mpeg4",
                                "-q:v", "5", str(path)], check=True)
                paths.append(path)
            value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                     "variants": ["Nồi chiên dùng cho cả nhà. Lòng nồi chống dính rất dễ rửa. Chỉ cần bấm nút chọn chế độ là xong."],
                     "visuals": [str(p) for p in paths], "mastering": {"schema_version": 1, "level": "balanced"},
                     "editorial": {"notes": ["Toàn cảnh nồi chiên trên bàn", "Cận cảnh lòng nồi chống dính", "Thao tác bấm nút chọn chế độ"]}}
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            spec = job.load_spec(spec_path)

            def inspect(path):
                return scene.probe(Path(FFPROBE), path)
            result = job.run(spec, root / "out", speaker=lambda status: FakeSpeaker(),
                             probe=lambda path: job.probe_duration_ms(Path(FFPROBE), path),
                             measure=lambda path: job.probe_size(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4",
                             inspect=inspect)
            self.assertEqual(result["state"], "COMPLETED", result)
            report = result["videos"][0]["mastering"]
            self.assertNotIn("fallback", report, report)
            self.assertEqual(report["mode"], "content")
            saved = m.load(root / "out" / ".dubflow-work" / report["plan"])
            by_unit = {}
            for segment in saved.segments:
                by_unit.setdefault(segment.speech_refs[0], set()).add(segment.source_id)
            self.assertEqual(by_unit["u2"], {"v2"})
            self.assertEqual(by_unit["u3"], {"v3"})
            log = json.loads((root / "out" / ".dubflow-work" / report["editorial"]).read_text(encoding="utf-8"))
            self.assertEqual(len(log["units"]), 3)
            self.assertTrue((root / "out" / "video-1.mp4").stat().st_size > 10_000)
            # The user pins the overview to the second sentence: planned again, the edit wins.
            value["editorial"]["edits"] = {"pin": {"u2": ["v1c1"]}}
            spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            again = job.run(job.load_spec(spec_path), root / "out", speaker=lambda status: FakeSpeaker(),
                            probe=lambda path: job.probe_duration_ms(Path(FFPROBE), path),
                            measure=lambda path: job.probe_size(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4",
                            inspect=inspect)
            report = again["videos"][0]["mastering"]
            self.assertFalse(report["reused"])
            saved = m.load(root / "out" / ".dubflow-work" / report["plan"])
            self.assertEqual(next(s for s in saved.segments if s.speech_refs == ("u2",)).source_id, "v1")

    def test_a_small_vision_budget_looks_again_for_the_sentence_it_missed(self):
        from engine.dubflow.mastering import analysis as scene, vision as seeing
        with TemporaryDirectory() as folder:
            root = Path(folder)
            paths = []
            for name, source in (("a.mp4", "testsrc2"), ("b.mp4", "smptebars"), ("c.mp4", "rgbtestsrc"), ("d.mp4", "testsrc")):
                path = root / name
                subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"{source}=s=640x360:r=30:d=8", "-c:v", "mpeg4",
                                "-q:v", "5", str(path)], check=True)
                paths.append(path)
            value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                     "variants": ["Nồi chiên dùng cho cả nhà. Lòng nồi chống dính rất dễ rửa. Chỉ cần bấm nút chọn chế độ là xong."],
                     "visuals": [str(p) for p in paths], "mastering": {"schema_version": 1, "level": "balanced"}}
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            what = {"v1": "Toàn cảnh nồi chiên dùng cho cả nhà", "v2": "Cận cảnh lòng nồi chống dính dễ rửa",
                    "v3": "Tay bấm nút chọn chế độ", "v4": "Hộp giấy đựng phụ kiện"}
            calls = []

            class Seeing:  # sees what each clip shows; three frames for four clips
                provider, used_model, budget = "gemini", "fake", 3

                def __init__(self, cache, context="", labels=()):
                    self.asked = self.taken = 0
                    self.warnings = []

                def describe(self, frames, cancelled):
                    calls.append([cid for cid, _ in frames])
                    self.taken += len(frames)
                    self.asked += len(frames)
                    return {cid: seeing.Seen(what[cid.split("c")[0]], (), (), "", (), 900, "clear", None, None, 0) for cid, _ in frames}

            result = job.run(job.load_spec(spec_path), root / "out", speaker=lambda status: FakeSpeaker(),
                             probe=lambda path: job.probe_duration_ms(Path(FFPROBE), path),
                             measure=lambda path: job.probe_size(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4",
                             inspect=lambda path: scene.probe(Path(FFPROBE), path), vision=Seeing)
            self.assertEqual(result["state"], "COMPLETED", result)
            report = result["videos"][0]["mastering"]
            log = json.loads((root / "out" / ".dubflow-work" / report["editorial"]).read_text(encoding="utf-8"))
            # A broad look spread over the clips (two frames, one kept back), then one look for an uncovered sentence.
            self.assertEqual(calls[0], ["v2c1", "v4c1"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(calls[1]), 1)
            self.assertEqual(log["vision"]["targeted"], 1)
            looked_for = calls[1][0]
            unit = next(u for u in log["units"] if looked_for in u["chosen"])
            self.assertEqual(unit["coverage"]["status"], "full")
            # the user leaves that shot out: the frames kept back never go to it again (#805)
            value["editorial"] = {"edits": {"exclude": [looked_for]}}
            spec_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            calls.clear()
            again = job.run(job.load_spec(spec_path), root / "out", speaker=lambda status: FakeSpeaker(),
                            probe=lambda path: job.probe_duration_ms(Path(FFPROBE), path),
                            measure=lambda path: job.probe_size(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4",
                            inspect=lambda path: scene.probe(Path(FFPROBE), path), vision=Seeing)
            self.assertEqual(again["state"], "COMPLETED", again)
            self.assertFalse([i for later in calls[1:] for i in later if i == looked_for], calls)


if __name__ == "__main__":
    unittest.main()

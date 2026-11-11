"""Adaptive multi-frame evidence (``dubflow.mastering.frames``) and the fallback keyframe of ``candidates.extract``."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from engine.dubflow.mastering import candidates as shots, evidence as ev, frames as more
from engine.dubflow.mastering.candidates import Candidate, Quality
from tests.mastering.regression import runner

GOOD = Quality(120, 50, 300, ())


def shot(cid, source, start, end, keyframe="k.jpg"):
    return Candidate(cid, source, start, end, 0, 50, GOOD, 1, keyframe)


def described(cid, text, confidence=800):
    found = ev.Evidence(cid)
    found.add(ev.Item("vision", confidence, text))
    return found


class Choosing(unittest.TestCase):
    def test_only_long_shots_near_a_weak_sentence_or_in_use_and_unsure_answers(self):
        pool = [shot("a", "v1", 0, 3000), shot("b", "v1", 3000, 10000), shot("c", "v2", 0, 8000), shot("d", "v2", 8000, 9000),
                shot("e", "v3", 0, 2000), shot("f", "v3", 2000, 4000, keyframe=None)]
        evidence = {c.candidate_id: described(c.candidate_id, "cảnh") for c in pool}
        evidence["e"] = described("e", "mờ", confidence=300)
        units = [{"unit_id": "u1", "chosen": ["a"], "coverage": {"status": "full", "needs_review": False}},
                 {"unit_id": "u2", "chosen": ["d"], "coverage": {"status": "partial", "needs_review": True}},
                 {"unit_id": "u3", "chosen": ["c"], "coverage": {"status": "full", "needs_review": False}}]
        self.assertEqual(more.wanted(units, pool, evidence, ()), ["c", "b", "e"])  # longest first
        self.assertEqual(more.wanted(units, pool, evidence, ("b",), locked=("c",)), ["e"])
        self.assertEqual(more.wanted(units, pool, evidence, (), limit=1), ["c"])

    def test_a_shot_a_user_decision_names_a_part_of_is_split_again_first(self):
        pool = [shot("v1c1", "v1", 0, 3000), shot("v1c2", "v1", 3000, 10000)]
        evidence = {c.candidate_id: described(c.candidate_id, "cảnh") for c in pool}
        self.assertEqual(more.wanted([], pool, evidence, (), again={"v1c1"}), ["v1c1"])
        self.assertEqual(more.parent("v1c2f3"), "v1c2")
        self.assertEqual(more.parent("v1c2"), "v1c2")

    def test_extra_frames_stay_inside_the_shot_clear_of_its_cuts(self):
        times = more.extra_times(shot("a", "v1", 2000, 12000))
        self.assertEqual(times, [3500, 10500])
        tiny = more.extra_times(shot("b", "v1", 0, 400))
        self.assertTrue(all(0 < t < 400 for t in tiny))


class Merging(unittest.TestCase):
    def test_frames_that_agree_are_more_evidence_of_the_same_shot(self):
        c = shot("v1c1", "v1", 0, 9000)
        found = described("v1c1", "Người bơi đeo đồng hồ dưới hồ bơi")
        out = more.merge(c, "Người bơi đeo đồng hồ dưới hồ bơi", [(1350, ev.Item("vision", 800, "Đồng hồ trên tay người bơi trong hồ bơi"), None, None)], found)
        self.assertEqual([p.candidate_id for p, _ in out], ["v1c1"])
        self.assertEqual(len(out[0][1].items), 2)

    def test_a_take_that_shows_different_things_is_split_between_its_frames(self):
        c = shot("v1c4", "v1", 0, 9000, keyframe="t-c4.jpg")
        found = described("v1c4", "Hộp giấy của loa trên bàn")
        found.add(ev.Item("transcript", 850, "loa chống nước"))
        frames = [(1350, ev.Item("vision", 800, "Tay mở hộp lấy loa ra"), "t-c4f1.jpg", 7),
                  (7650, ev.Item("vision", 800, "Loa bị xịt nước dưới vòi sen vẫn phát nhạc"), "t-c4f3.jpg", 9)]
        out = more.merge(c, "Hộp giấy của loa trên bàn", frames, found)
        self.assertEqual([(p.candidate_id, p.start_ms, p.end_ms, p.keyframe, p.dhash) for p, _ in out],
                         [("v1c4f1", 0, 2925, "t-c4f1.jpg", 7), ("v1c4f2", 2925, 6075, "t-c4.jpg", 1),
                          ("v1c4f3", 6075, 9000, "t-c4f3.jpg", 9)])
        texts = [[i.text for i in e.items] for _, e in out]
        self.assertEqual(texts[2], ["loa chống nước", "Loa bị xịt nước dưới vòi sen vẫn phát nhạc"])  # shared + its own frame
        self.assertIn("Hộp giấy của loa trên bàn", texts[1])
        self.assertTrue(all(p.end_ms - p.start_ms >= shots.MIN_CANDIDATE_MS for p, _ in out))


class FallbackKeyframe(unittest.TestCase):
    def test_a_dark_middle_frame_is_replaced_by_the_first_usable_frame(self):
        grabbed = []
        dark, sharp = bytes(160 * 90), bytes((x * 7 + y * 13) % 256 for y in range(90) for x in range(160))

        def run(argv, folder, cancelled):
            at = int(float(argv[argv.index("-ss") + 1]) * 1000)
            grabbed.append(at)
            (folder / argv[-1]).write_bytes(dark if at == 5000 else sharp)
            jpeg = next(a for a in argv if a.endswith(".jpg"))
            (folder / jpeg).write_bytes(b"jpeg")

        found = type("A", (), {})()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            video = folder / "v.mp4"
            video.write_bytes(b"x")
            original = shots.windows
            shots.windows = lambda _: [(0, 10000, 0, 50)]
            try:
                made = shots.extract(video, "v1", found, folder, ffmpeg=Path("ffmpeg"), run=run)
            finally:
                shots.windows = original
        self.assertEqual(grabbed, [5000, 2500])
        self.assertTrue(made[0].quality.usable)


class Harness(unittest.TestCase):
    """The regression runner's frame step on a scenario written for this test (not a held-out set)."""

    SCENARIO = {
        "schema_version": 1, "name": "test-long-take", "split": "dev", "forward_only": False, "pacing": "natural",
        "candidates": [
            {"id": "a1", "source": "a", "start_ms": 0, "end_ms": 3000, "text": "Ấm siêu tốc inox trên bàn bếp"},
            {"id": "b1", "source": "b", "start_ms": 0, "end_ms": 9000, "text": "Rót nước vào ấm siêu tốc",
             "frames": [{"at_ms": 7500, "text": "Nước sôi sùi bọt, đèn báo tự ngắt chuyển màu xanh"}]},
            {"id": "c1", "source": "c", "start_ms": 0, "end_ms": 4000, "text": "Khung hình tối", "usable": False,
             "frames": [{"at_ms": 1000, "text": "Lau thân ấm bằng khăn mềm"}]},
        ],
        "units": [{"id": "u1", "text": "Ấm siêu tốc inox sáng bóng.", "ms": 3000},
                  {"id": "u2", "text": "Nước sôi là tự ngắt, đèn báo chuyển xanh.", "ms": 3000},
                  {"id": "u3", "text": "Lau thân ấm bằng khăn mềm là sạch.", "ms": 3000}],
        "truth": {"schema_version": 1, "dataset": "test-long-take", "split": "dev", "units": {
            "u1": {"accept": [{"source": "a", "start_ms": 0, "end_ms": 3000}]},
            "u2": {"accept": [{"source": "b", "start_ms": 6000, "end_ms": 9000}]},
            "u3": {"accept": [{"source": "c", "start_ms": 0, "end_ms": 4000}]}}},
    }

    def test_the_late_part_of_a_long_take_and_a_shot_with_a_dark_middle_are_found(self):
        result = runner.run(self.SCENARIO)
        units = {u["unit"]: u for u in result["alignment"]["units"]}
        self.assertGreater(result["extra_frames"], 0)
        # the late part of the take now illustrates the sentence (before: nothing did); the last 75 ms of the
        # sentence take a filler, so the part plays 2 s, not all of its 2.9 s
        u2 = next(d for d in result["decisions"] if d["unit_id"] == "u2")
        self.assertEqual(u2["chosen"][0], "b1f3")
        self.assertGreaterEqual(units["u2"]["coverage_milli"], 600)
        self.assertGreaterEqual(units["u3"]["coverage_milli"], 900)
        self.assertEqual(result["problems"], [])


if __name__ == "__main__":
    unittest.main()

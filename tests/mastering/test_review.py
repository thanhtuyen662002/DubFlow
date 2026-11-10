"""Review of an editorial plan (Phase 5 MVP): the planner's verdict per sentence, the user's decisions kept apart,
persisted, undoable, invalidated when the footage changes, and fed back to the planner as pins and exclusions."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import review as rv
from engine.dubflow.product import job

from tests.mastering.test_product_planner import FakeSpeaker, analysis


def cand(cid: str, *, text: str = "", problems: list[str] | None = None, signal_only: bool = False) -> dict:
    items = [{"origin": "signal", "confidence": 500, "text": "", "labels": [], "detail": ""}]
    if text and not signal_only:
        items.append({"origin": "vision", "confidence": 800, "text": text, "labels": [], "detail": ""})
    return {"id": cid, "source_id": "v1", "start_ms": 0, "end_ms": 2000, "keyframe": f"{cid}.jpg",
            "quality": {"problems": problems or []}, "evidence": {"items": items}, "summary": text}


def unit(uid: str, *, matched: int = 0, status: str = "full", needs_review: bool = False, chosen=("c1",), function="feature"):
    return {"unit_id": uid, "text": f"câu {uid}", "function": function, "start_ms": 0, "end_ms": 1000,
            "chosen": list(chosen), "alternatives": [{"candidate_id": "c2", "score": 400, "reasons": ["x"]}],
            "notes": [], "coverage": {"matched_footage": matched, "status": status, "needs_review": needs_review}}


def log(units, candidates, mode="content") -> dict:
    return {"schema_version": 1, "kind": "editorial_decisions", "plan_id": "product-1", "mode": mode, "units": units,
            "candidates": candidates, "edits": {"pin": {}, "exclude": [], "exclude_sources": []}, "warnings": ["Cảnh báo A"]}


class VerdictTests(unittest.TestCase):
    def test_states_separate_a_found_match_from_footage_never_looked_at_and_footage_that_lacks_it(self):
        everything_seen = [cand("c1", text="vòi phun"), cand("c2", text="hộp đựng")]
        partly_seen = [cand("c1", text="vòi phun"), cand("c2")]
        found = log([unit("u1", matched=2)], everything_seen)
        self.assertEqual(rv.evidence_state(found["units"][0], found), rv.MATCH_AVAILABLE)
        missing = unit("u2", matched=0, status="none", needs_review=True)
        # every usable shot was described and none matches: the footage given does not show it
        self.assertEqual(rv.evidence_state(missing, log([missing], everything_seen)), rv.SOURCE_INSUFFICIENT)
        # one shot was never looked at: not found, but the footage may still hold it
        self.assertEqual(rv.evidence_state(missing, log([missing], partly_seen)), rv.MATCH_NOT_FOUND)
        # without content evidence at all it can never be called insufficient
        self.assertEqual(rv.evidence_state(missing, log([missing], everything_seen, mode="signals_only")), rv.MATCH_NOT_FOUND)
        generic = unit("u3", status="generic", function="cta")
        self.assertEqual(rv.evidence_state(generic, log([generic], partly_seen)), rv.NOT_NEEDED)

    def test_an_unusable_shot_does_not_block_insufficient(self):
        missing = unit("u2", status="none", needs_review=True)
        shots = [cand("c1", text="vòi phun"), cand("c2", problems=["dark"])]
        self.assertEqual(rv.evidence_state(missing, log([missing], shots)), rv.SOURCE_INSUFFICIENT)

    def test_ready_needs_coverage_content_and_confident_segments(self):
        shots = [cand("c1", text="vòi phun"), cand("c2", text="hộp")]
        good = log([unit("u1", matched=1)], shots)
        rv.annotate(good, {"segments": [{"speech_refs": ["u1"], "needs_review": False}]})
        self.assertEqual(good["units"][0]["review"], {"evidence": rv.MATCH_AVAILABLE, "state": rv.READY})
        weak = log([unit("u1", matched=1)], shots)
        rv.annotate(weak, {"segments": [{"speech_refs": ["u1"], "needs_review": True}]})
        self.assertEqual(weak["units"][0]["review"]["state"], rv.NEEDS_REVIEW)
        partial = log([unit("u1", matched=1, status="partial", needs_review=True)], shots)
        rv.annotate(partial)
        self.assertEqual(partial["units"][0]["review"]["state"], rv.NEEDS_REVIEW)
        self.assertEqual(partial["review_summary"], {rv.NEEDS_REVIEW: 1})


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.log = rv.annotate(log([unit("u1", matched=0, status="none", needs_review=True), unit("u2", matched=1)],
                                   [cand("c1", text="a"), cand("c2", text="b"), cand("c3")]))

    def test_pin_exclude_approve_undo_and_the_planner_edits(self):
        review = rv.Review()
        self.assertEqual(rv.unreviewed(self.log, review), ["u1"])
        review.decide(self.log, "u1", "pin", ["c2"])
        review.decide(self.log, "u2", "exclude", ["c3"])
        self.assertEqual(rv.unreviewed(self.log, review), [])  # replaced by the user
        edits = review.edits()
        self.assertEqual(edits.pin, {"u1": ["c2"]})
        self.assertEqual(edits.exclude, {"c3"})
        review.decide(self.log, "u2", "pin", ["c3"])  # pinning what was excluded un-excludes it
        self.assertEqual(review.edits().exclude, set())
        self.assertTrue(review.undo())
        self.assertEqual(review.edits().exclude, {"c3"})
        self.assertEqual(review.revision, 4)
        with self.assertRaises(rv.ReviewError):
            review.decide(self.log, "u1", "pin", ["nope"])
        with self.assertRaises(rv.ReviewError):
            review.decide(self.log, "u9", "approve")

    def test_approval_is_the_users_word_and_does_not_change_the_verdict(self):
        review = rv.Review()
        review.decide(self.log, "u1", "approve")
        self.assertEqual(rv.user_state(review.units["u1"]), rv.USER_APPROVED)
        shown = rv.view(self.log, None, review)
        first = shown["units"][0]
        self.assertEqual(first["state"], rv.MATCH_NOT_FOUND)  # still the planner's verdict
        self.assertEqual(first["user_state"], rv.USER_APPROVED)
        self.assertEqual(shown["unreviewed"], [])

    def test_changed_footage_invalidates_an_approval_and_a_pin_to_a_gone_shot(self):
        review = rv.Review()
        review.decide(self.log, "u1", "approve")
        review.decide(self.log, "u2", "pin", ["c3"])
        again = json.loads(json.dumps(self.log))
        again["units"][0]["chosen"] = ["c2"]
        again["candidates"] = [c for c in again["candidates"] if c["id"] != "c3"]
        messages = review.reconcile(again)
        self.assertEqual(len(messages), 2)
        self.assertEqual(rv.user_state(review.units["u1"]), rv.INVALIDATED)
        self.assertEqual(rv.user_state(review.units["u2"]), rv.INVALIDATED)
        self.assertEqual(review.edits().pin, {})  # an invalid pin is not given to the planner
        self.assertIn("u1", rv.unreviewed(rv.annotate(again), review))

    def test_round_trip_rejects_unknown_fields_and_keeps_acknowledged_warnings(self):
        review = rv.Review()
        review.acknowledge("Cảnh báo A")
        review.decide(self.log, "u1", "exclude", ["c1"])
        loaded = rv.Review.from_dict(json.loads(json.dumps(review.to_dict())))
        self.assertEqual(loaded.to_dict(), review.to_dict())
        self.assertTrue(rv.view(self.log, None, loaded)["warnings"][0]["acknowledged"])
        bad = review.to_dict()
        bad["units"]["u1"]["filter"] = "eq=gamma=9"
        with self.assertRaises(rv.ReviewError):
            rv.Review.from_dict(bad)
        with self.assertRaises(rv.ReviewError):
            rv.Review.from_dict({**review.to_dict(), "schema_version": 2})


class CliTests(unittest.TestCase):
    def test_the_desktop_calls_change_the_file_and_print_the_view(self):
        with TemporaryDirectory() as folder:
            work = Path(folder)
            data = rv.annotate(log([unit("u1", matched=0, status="none", needs_review=True)], [cand("c1", text="a"), cand("c2")]))
            (work / "editorial-1.json").write_text(json.dumps(data), encoding="utf-8")

            def call(*args) -> tuple[int, dict]:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = rv.main(["--work", str(work), "--video", "1", *args])
                return code, json.loads(out.getvalue())
            code, shown = call("view")
            self.assertEqual((code, shown["unreviewed"]), (0, ["u1"]))
            self.assertFalse((work / "review-1.json").exists())  # looking writes nothing
            code, shown = call("decide", "u1", "pin", "c2")
            self.assertEqual(shown["units"][0]["decision"]["pin"], ["c2"])
            code, shown = call("ack", rv.warning_id("Cảnh báo A"))
            self.assertTrue(shown["warnings"][0]["acknowledged"])
            code, shown = call("undo")
            self.assertFalse(shown["warnings"][0]["acknowledged"])
            code, shown = call("decide", "u1", "pin", "../../etc")
            self.assertEqual(code, 2)
            self.assertIn("error", shown)
            self.assertEqual(rv.load(work / "review-1.json").units["u1"].pin, ["c2"])


class JobReviewTests(unittest.TestCase):
    def test_a_pin_in_the_review_reaches_the_plan_and_only_that_video_renders_again(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            clip = root / "clip.mp4"
            clip.write_bytes(b"mp4")
            value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                     "variants": ["Nồi chiên không dầu dung tích lớn. Mua ngay hôm nay.", "Nồi chiên đẹp. Mua ngay."],
                     "visuals": [str(clip)], "mastering": {"schema_version": 1, "level": "balanced"}}
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(value), encoding="utf-8")
            commands: list = []

            def ffmpeg(argv, cwd, cancelled):
                commands.append(argv)
                Path(argv[-1]).write_bytes(b"mp4")
            info = scene.StreamInfo(20_000, 1080, 1920, 30, True)
            out = root / "out"

            def render(spec):
                return job.run(spec, out, speaker=lambda status: FakeSpeaker(), probe=lambda path: 20_000,
                               measure=lambda path: (1200, 1200), ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg,
                               inspect=lambda path: info)
            with mock.patch.object(scene, "analyse", return_value=analysis(20_000, [2500, 6000, 13_000])):
                first = render(job.load_spec(spec_path))
                self.assertEqual(first["state"], "COMPLETED")
                work = out / ".dubflow-work"
                data = json.loads((work / "editorial-1.json").read_text(encoding="utf-8"))
                self.assertIn("review", data["units"][0])
                self.assertIn("review_states", first["videos"][0]["mastering"])
                other = [c["id"] for c in data["candidates"] if c["id"] not in data["units"][0]["chosen"]][0]
                review = rv.Review()
                review.decide(data, data["units"][0]["unit_id"], "pin", [other])
                rv.save(rv.path_for(work, 1), review)
                renders = len([c for c in commands if str(c[-1]).endswith(".partial.mp4")])
                again = render(replace(job.load_spec(spec_path), only=(1,)))
                self.assertEqual(again["state"], "COMPLETED")
                self.assertEqual([v["index"] for v in again["videos"]], [1, 2])  # video 2 kept as it was
                self.assertEqual(len([c for c in commands if str(c[-1]).endswith(".partial.mp4")]) - renders, 1)
                data = json.loads((work / "editorial-1.json").read_text(encoding="utf-8"))
                self.assertEqual(data["units"][0]["chosen"][0], other)
                self.assertFalse(again["videos"][0]["mastering"]["reused"])  # planned again with the pin

    def test_only_must_name_existing_videos(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "c.mp4").write_bytes(b"x")
            value = {"schema_version": 1, "name": "A", "price": "", "voice_id": "v", "variants": ["Một câu."],
                     "visuals": [str(root / "c.mp4")], "only": [2]}
            (root / "s.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(job.ProductError):
                job.load_spec(root / "s.json")


if __name__ == "__main__":
    unittest.main()

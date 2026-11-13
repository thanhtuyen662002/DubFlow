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

    def test_a_near_miss_is_not_found_never_insufficient(self):
        # rc5 Windows: shots that showed the sentence in other words (relevance 37 and 57, under a match) were
        # reported as footage the source does not have
        everything_seen = [cand("c1", text="vòi phun"), cand("c2", text="hộp đựng")]
        near = unit("u2", status="none", needs_review=True)
        near["coverage"]["best_relevance"] = 37
        self.assertEqual(rv.evidence_state(near, log([near], everything_seen)), rv.MATCH_NOT_FOUND)
        near["coverage"]["best_relevance"] = 0
        self.assertEqual(rv.evidence_state(near, log([near], everything_seen)), rv.SOURCE_INSUFFICIENT)

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

    def test_an_approved_pin_stays_approved_after_the_render_that_applies_it(self):
        # rc5 P2-5: Ghim → Duyệt cảnh này → Dựng lại invalidated the approval although nothing but the pin changed
        review = rv.Review()
        review.decide(self.log, "u1", "pin", ["c2"])
        review.decide(self.log, "u1", "approve")  # the plan on screen still shows c1
        self.assertEqual(review.units["u1"].approved, ["c2"])
        again = json.loads(json.dumps(self.log))
        again["units"][0]["chosen"] = ["c2"]  # the re-render applied the pin
        self.assertEqual(review.reconcile(again), [])
        reopened = rv.Review.from_dict(json.loads(json.dumps(review.to_dict())))  # close and open the app
        self.assertEqual(rv.user_state(reopened.units["u1"]), rv.USER_APPROVED)
        self.assertEqual(reopened.reconcile(again), [])  # and a second render with the same plan
        self.assertEqual(rv.user_state(reopened.units["u1"]), rv.USER_APPROVED)
        self.assertEqual(reopened.edits().pin, {"u1": ["c2"]})
        self.assertNotIn("u1", rv.unreviewed(rv.annotate(again), reopened))

    def test_an_approval_saved_before_the_fix_with_a_pin_is_kept_when_the_pin_is_applied(self):
        saved = {"schema_version": 1, "kind": "editorial_review", "plan_id": "product-1", "revision": 2,
                 "units": {"u1": {"approved": ["c1"], "pin": ["c2"], "exclude": []}}, "acknowledged": [], "history": []}
        review = rv.Review.from_dict(saved)
        again = json.loads(json.dumps(self.log))
        again["units"][0]["chosen"] = ["c2"]
        self.assertEqual(review.reconcile(again), [])
        self.assertEqual(rv.user_state(review.units["u1"]), rv.USER_APPROVED)

    def test_an_approved_pin_is_invalidated_only_for_a_real_reason(self):
        def approved_pin():
            review = rv.Review()
            review.decide(self.log, "u1", "pin", ["c2"])
            review.decide(self.log, "u1", "approve")
            return review
        # the source changed: the pinned shot is gone
        review = approved_pin()
        gone = json.loads(json.dumps(self.log))
        gone["candidates"] = [c for c in gone["candidates"] if c["id"] != "c2"]
        self.assertEqual(len(review.reconcile(gone)), 1)
        self.assertEqual(rv.user_state(review.units["u1"]), rv.INVALIDATED)
        # the plan could not honour the pin: the sentence shows something else
        review = approved_pin()
        other = json.loads(json.dumps(self.log))
        other["units"][0]["chosen"] = ["c3"]
        self.assertEqual(len(review.reconcile(other)), 1)
        self.assertEqual(rv.user_state(review.units["u1"]), rv.INVALIDATED)
        # the pin was shorter than the sentence and the planner filled the rest after it: still the pin, still approved
        review = approved_pin()
        padded = json.loads(json.dumps(self.log))
        padded["units"][0]["chosen"] = ["c2", "c3"]
        self.assertEqual(review.reconcile(padded), [])
        self.assertEqual(rv.user_state(review.units["u1"]), rv.USER_APPROVED)
        # ... but a later plan that drops the pinned shot from the front is a change
        moved = json.loads(json.dumps(self.log))
        moved["units"][0]["chosen"] = ["c3", "c2"]
        self.assertEqual(len(review.reconcile(moved)), 1)
        # the sentence is gone from the script
        review = approved_pin()
        removed = json.loads(json.dumps(self.log))
        removed["units"] = removed["units"][1:]
        self.assertEqual(len(review.reconcile(removed)), 1)
        # the user changed the decision: a new pin drops the approval (not an invalidation)
        review = approved_pin()
        review.decide(self.log, "u1", "pin", ["c3"])
        self.assertIsNone(review.units["u1"].approved)
        self.assertIsNone(rv.user_state(review.units["u1"]))

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
            rv.Review.from_dict({**review.to_dict(), "schema_version": rv.SCHEMA_VERSION + 1})


def footage(cid: str, start: int, end: int, *, source: str = "v1", text: str = "x") -> dict:
    """A shot as a decision log records it, with everything a candidate needs (#805)."""
    return {**cand(cid, text=text), "source_id": source, "start_ms": start, "end_ms": end, "shot": 0, "motion": 50,
            "dhash": "1", "quality": {"brightness": 120, "contrast": 50, "sharpness": 300, "problems": []}}


def split_log(pieces=(("c3f1", 4000, 5000), ("c3f2", 5000, 6000), ("c3f3", 6000, 7000))) -> dict:
    shots = [footage("c1", 0, 2000), footage("c2", 2000, 4000), *(footage(i, a, b) for i, a, b in pieces)]
    u1 = unit("u1", matched=1, chosen=(pieces[0][0],))
    u2 = unit("u2", matched=1, chosen=(pieces[-1][0], "c1"))
    return rv.annotate(log([u1, u2], shots))


class WholeShotExclusionTests(unittest.TestCase):  # #805
    def left(self, review: rv.Review, the_log: dict) -> set[str]:
        from engine.dubflow.mastering import align
        return align.exclusion(review.edits(), rv._log_candidates(the_log)).gone

    def test_b1_the_whole_shot_leaves_out_every_piece_and_the_panel_says_which(self):
        the_log = split_log()
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c3"])
        self.assertEqual(review.edits().exclude, {"c3"})
        self.assertEqual(review.edits().exclude_ranges, {"c3": ("v1", 4000, 7000)})  # the footage, not the id only
        self.assertEqual(self.left(review, the_log), {"c3f1", "c3f2", "c3f3"})
        entry = review.records[-1]
        self.assertEqual((entry["type"], entry["detail"]), ("EXCLUDE", "whole_shot"))
        self.assertEqual([a[0] for a in entry["affected"]], ["c3f1", "c3f2", "c3f3"])
        shown = rv.view(the_log, None, review)
        family = shown["families"]["c3"]
        self.assertEqual((family["start_ms"], family["end_ms"]), (4000, 7000))
        self.assertEqual([(p["id"], p["used_by"]) for p in family["pieces"]], [("c3f1", ["u1"]), ("c3f2", []), ("c3f3", ["u2"])])
        self.assertEqual(shown["exclusions"], [{"id": "c3", "by": "u1", "whole": True, "covers": ["c3f1", "c3f2", "c3f3"],
                                                "reused": []}])
        marks = {s["id"]: (s["parent"], s["excluded"], s["excluded_by"]) for s in shown["shots"]}
        self.assertEqual(marks["c3f2"], ("c3", True, "u1"))
        self.assertEqual(marks["c1"], ("c1", False, None))

    def test_b2_b3_an_alternative_or_a_piece_leaves_out_only_itself(self):
        the_log = split_log()
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c2"])  # an alternative the sentence does not use
        self.assertEqual(self.left(review, the_log), {"c2"})
        review.decide(the_log, "u2", "exclude", ["c3f2"])  # a piece: its siblings stay (no silent widening)
        self.assertEqual(self.left(review, the_log), {"c2", "c3f2"})
        self.assertNotIn("detail", review.records[-1])
        shown = rv.view(the_log, None, review)
        self.assertEqual([(e["id"], e["by"], e["whole"], e["covers"]) for e in shown["exclusions"]],
                         [("c2", "u1", False, ["c2"]), ("c3f2", "u2", False, ["c3f2"])])

    def test_b4_the_exclusion_survives_a_new_split_with_other_ids(self):
        review = rv.Review()
        review.decide(split_log(), "u1", "exclude", ["c3"])
        # the next run splits the same footage at other places, under other ids
        again = split_log((("c7f1", 4000, 5500), ("c7f2", 5500, 7000)))
        self.assertEqual(review.reconcile(again), [])
        self.assertFalse(review.units["u1"].invalidated)
        self.assertEqual(self.left(review, again), {"c7f1", "c7f2"})
        # the same ids on other footage (the source changed): not what the user left out, and the decision says the
        # footage is gone instead of leaving out other footage by its name
        moved = split_log((("c3f1", 7000, 8000), ("c3f2", 8000, 9000), ("c3f3", 9000, 10_000)))
        self.assertEqual(self.left(review, moved), set())
        self.assertEqual(len(review.reconcile(moved)), 1)
        self.assertEqual(review.conflicts["u1"]["cause"], "shot_gone")
        review2 = rv.Review()
        review2.decide(split_log(), "u1", "exclude", ["c3f2"])
        self.assertEqual(self.left(review2, split_log((("c9f1", 5000, 6000),))), {"c9f1"})

    def test_b5_kept_closed_reopened_and_undone(self):
        the_log = split_log()
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c3"])
        saved = json.loads(json.dumps(review.to_dict()))
        self.assertEqual(saved["schema_version"], 3)
        self.assertEqual(saved["units"]["u1"]["ranges"], {"c3": ["v1", 4000, 7000]})
        reopened = rv.Review.from_dict(saved)
        self.assertEqual(reopened.edits().exclude_ranges, {"c3": ("v1", 4000, 7000)})
        self.assertEqual(self.left(reopened, the_log), {"c3f1", "c3f2", "c3f3"})
        # "Bỏ loại" from any piece lets the whole shot back in, as one change
        before = reopened.revision
        reopened.decide(the_log, "u2", "include", ["c3f2"])
        self.assertEqual(reopened.revision, before + 1)
        self.assertEqual(self.left(reopened, the_log), set())
        self.assertEqual((reopened.records[-1]["type"], reopened.records[-1]["detail"], reopened.records[-1]["shots"]),
                         ("UNDO", "include", ["c3"]))
        self.assertTrue(reopened.undo())
        self.assertEqual(self.left(reopened, the_log), {"c3f1", "c3f2", "c3f3"})
        with self.assertRaises(rv.ReviewError):
            reopened.decide(the_log, "u1", "include", ["c1"])  # not excluded
        # a review saved before (v2, no ranges) still reads
        old = json.loads(json.dumps(saved))
        old["schema_version"] = 2
        del old["units"]["u1"]["ranges"]
        self.assertEqual(rv.Review.from_dict(old).edits().exclude, {"c3"})
        bad = json.loads(json.dumps(saved))
        bad["units"]["u1"]["ranges"] = {"c3": ["v1", 7000, 4000]}
        with self.assertRaises(rv.ReviewError):
            rv.Review.from_dict(bad)

    def test_b6_leaving_everything_out_is_refused_and_a_reuse_is_a_conflict(self):
        the_log = split_log()
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c1", "c2"])
        with self.assertRaisesRegex(rv.ReviewError, "không còn cảnh nào"):
            review.decide(the_log, "u2", "exclude", ["c3"])
        self.assertEqual(review.edits().exclude, {"c1", "c2"})  # nothing changed
        # a shot excluded in one sentence cannot be pinned in another, and a pinned shot cannot be excluded elsewhere
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c3"])
        with self.assertRaisesRegex(rv.ReviewError, "đã bị loại ở câu 1"):
            review.decide(the_log, "u2", "pin", ["c3f2"])
        review = rv.Review()
        review.decide(the_log, "u2", "pin", ["c3f2"])
        with self.assertRaisesRegex(rv.ReviewError, "Câu 2 đang ghim"):
            review.decide(the_log, "u1", "exclude", ["c3"])
        # the plan had to use footage left out (another part, a changed source): the sentence gets a conflict
        review = rv.Review()
        review.decide(the_log, "u1", "exclude", ["c3"])
        used = json.loads(json.dumps(the_log))
        used["exclusions"] = {"left_out": {}, "widened": {}, "reused": ["c3f1"]}
        messages = review.reconcile(used)
        self.assertEqual(len(messages), 1)
        self.assertEqual(review.conflicts["u1"]["cause"], "all_excluded")
        self.assertEqual(review.conflicts["u1"]["options"][0], "Bỏ loại trừ cảnh")
        review.decide(used, "u1", "include", ["c3"])
        self.assertFalse(review.units["u1"].invalidated)


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
                target = Path(argv[-1])
                (target if target.is_absolute() else Path(cwd, target)).write_bytes(b"mp4")
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
                review.decide(data, data["units"][0]["unit_id"], "approve")  # P2-5: approved before the render
                rv.save(rv.path_for(work, 1), review)
                renders = len([c for c in commands if str(c[-1]).endswith(".partial.mp4")])
                again = render(replace(job.load_spec(spec_path), only=(1,)))
                self.assertEqual(again["state"], "COMPLETED")
                self.assertEqual([v["index"] for v in again["videos"]], [1, 2])  # video 2 kept as it was
                self.assertEqual(len([c for c in commands if str(c[-1]).endswith(".partial.mp4")]) - renders, 1)
                data = json.loads((work / "editorial-1.json").read_text(encoding="utf-8"))
                self.assertEqual(data["units"][0]["chosen"][0], other)
                self.assertFalse(again["videos"][0]["mastering"]["reused"])  # planned again with the pin
                kept = rv.load(rv.path_for(work, 1))  # as the app reads it when opened again
                self.assertEqual(rv.user_state(kept.units[data["units"][0]["unit_id"]]), rv.USER_APPROVED)
                self.assertFalse(any("đã đổi" in w for w in again["warnings"]) if "warnings" in again else False)

    def test_only_must_name_existing_videos(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "c.mp4").write_bytes(b"x")
            value = {"schema_version": 1, "name": "A", "price": "", "voice_id": "v", "variants": ["Một câu."],
                     "visuals": [str(root / "c.mp4")], "only": [2]}
            (root / "s.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(job.ProductError):
                job.load_spec(root / "s.json")


class AlternativesTests(unittest.TestCase):
    def test_a_shot_the_sentence_already_uses_is_not_offered_as_an_alternative(self):
        """rc2 Windows check (D4): "Dùng cảnh này" on a shot already in use only pinned it and changed nothing."""
        item = unit("u1", matched=2, chosen=("c1",))
        item["alternatives"] = [{"candidate_id": "c1", "score": 736, "reasons": []}, {"candidate_id": "c2", "score": 400, "reasons": []}]
        document = log([item], [cand("c1", text="vòi phun"), cand("c2", text="hộp đựng")])
        shown = rv.view(document, None, rv.Review())["units"][0]["alternatives"]
        self.assertEqual([alt["id"] for alt in shown], ["c2"])


if __name__ == "__main__":
    unittest.main()

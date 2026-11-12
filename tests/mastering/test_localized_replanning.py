"""Gate A, localized replanning (P2-N5): one decision re-plans its sentence, never voids unrelated approvals.

A1 single replacement · A2 exclusion conflict · A3 source and sentence change · A4 multi-part (forward only) ·
A5 randomized replanning. An approval is a constraint of the next plan; an invalidation happens only for a real
cause, and its record says which (the conflict record), never silently.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.mastering import align
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering import review as rv
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering.planners import product as planner
from engine.dubflow.product import job
from tests.mastering.test_product_planner import FakeSpeaker, analysis

PACING = rules.STYLE_PACING["natural"]
WORDS = ["nồi chiên", "lòng nồi", "nút bấm", "khay nướng", "gà rán", "khoai tây", "màn hình", "tay cầm", "nắp kính",
         "dây điện", "bàn bếp", "đĩa ăn"]
REAL_CAUSES = {"sentence_removed", "sentence_changed", "source_changed", "timing_changed", "shot_gone", "excluded",
               "pinned_elsewhere", "shown_again"}


class World:
    """Sources cut into shots, each described by the vision; sentences that speak of some of them."""

    def __init__(self, seed: int, *, forward_only: bool = False, sources: int | None = None) -> None:
        rnd = random.Random(seed)
        self.forward_only = forward_only
        self.pool: list[shots.Candidate] = []
        self.notes: dict[str, str] = {}
        for s in range(sources or rnd.randint(2, 4)):
            at = 0
            for k in range(rnd.randint(2, 5)):
                length = rnd.choice([1500, 2500, 4000, 6000])
                cid = f"v{s + 1}c{k + 1}"
                self.pool.append(shots.Candidate(cid, f"v{s + 1}", at, at + length, k, 50, shots.Quality(120, 50, 300),
                                                 rnd.getrandbits(64)))
                self.notes[cid] = "Cận cảnh " + " và ".join(rnd.sample(WORDS, 2))
                at += length
        self.identity = {f"v{s + 1}": f"id-{s + 1}" for s in range(len({c.source_id for c in self.pool}))}
        n = rnd.randint(4, 8)
        self.spoken = [(f"{rnd.choice(['Đây là', 'Bạn thấy', 'Nhìn kìa'])} {w} rất đẹp.", rnd.choice([1500, 2200, 3000]))
                       for w in rnd.sample(WORDS, n)]

    def plan(self, edits: align.Edits | None = None, *, plan_id: str = "p1") -> dict:
        found = ev.gather(c.candidate_id for c in self.pool)
        for c in self.pool:
            found[c.candidate_id].add(ev.Item("vision", 900, self.notes[c.candidate_id], tuple(ev.cue_labels(self.notes[c.candidate_id]))))
        total = 300 + sum(d for _, d in self.spoken) + 250 * (len(self.spoken) - 1) + 700
        units = align.units(self.spoken, total, lead_ms=300, gap_ms=250)
        _, decisions, mode = align.assign(units, self.pool, found, PACING, forward_only=self.forward_only, edits=edits)
        return {"plan_id": plan_id, "mode": mode, "units": [d.to_dict() for d in decisions],
                "candidates": [{**c.to_dict(), "id": c.candidate_id} for c in self.pool],
                "edits": (edits or align.Edits()).to_dict(), "sources": dict(self.identity), "source_identity": "content"}


def approve_all(log: dict) -> rv.Review:
    review = rv.Review()
    for unit in log["units"]:
        if unit["chosen"]:
            review.decide(log, unit["unit_id"], "approve")
    return review


def approvals(review: rv.Review) -> dict[str, list[str]]:
    return {k: list(v.approved) for k, v in review.units.items() if v.approved is not None and not v.invalidated}


def replan(world: World, review: rv.Review, *, plan_id: str = "p2") -> tuple[dict, list[str]]:
    new = world.plan(review.edits(), plan_id=plan_id)
    return new, review.reconcile(new, run="run-2")


def pieces(log: dict, unit_id: str) -> list:
    return next(u for u in log["units"] if u["unit_id"] == unit_id).get("pieces")


class SingleReplacementTests(unittest.TestCase):  # A1
    def test_replacing_the_shot_of_one_sentence_keeps_every_other_approval_and_its_pieces(self):
        checked = 0
        for seed in range(40):
            world = World(seed)
            first = world.plan()
            review = approve_all(first)
            target = first["units"][seed % len(first["units"])]
            others = [c["id"] for c in first["candidates"]
                      if not any(c["id"] in u["chosen"] for u in first["units"])]
            if not others:
                continue
            before = approvals(review)
            review.decide(first, target["unit_id"], "pin", [others[0]])
            new, messages = replan(world, review)
            self.assertEqual(messages, [], seed)
            after = approvals(review)
            for unit_id, shown in before.items():
                if unit_id == target["unit_id"]:
                    continue
                self.assertEqual(after.get(unit_id), shown, (seed, unit_id))
                self.assertEqual(pieces(new, unit_id), pieces(first, unit_id), (seed, unit_id))  # exactly as approved
            self.assertEqual(next(u for u in new["units"] if u["unit_id"] == target["unit_id"])["chosen"][0], others[0])
            checked += 1
        self.assertGreater(checked, 10)

    def test_approving_alone_never_changes_the_plan_identity(self):
        world = World(3)
        first = world.plan()
        review = approve_all(first)
        self.assertTrue(review.edits().keep)
        self.assertEqual(review.edits().to_dict(), align.Edits().to_dict())  # the cache key: nothing to re-render
        again = world.plan(review.edits())["units"]
        for key in ("chosen", "pieces", "coverage"):
            self.assertEqual([u.get(key) for u in again], [u.get(key) for u in first["units"]], key)


class ExclusionConflictTests(unittest.TestCase):  # A2
    def test_excluding_a_shot_another_sentence_approved_is_a_recorded_conflict_never_silent(self):
        found = 0
        for seed in range(80):
            world = World(seed)
            first = world.plan()
            review = approve_all(first)
            owners: dict[str, list[str]] = {}
            for unit in first["units"]:
                for cid in unit["chosen"]:
                    owners.setdefault(cid, []).append(unit["unit_id"])
            shared = next((cid for cid, ids in owners.items() if len(set(ids)) > 1), None)
            if shared is None:
                continue
            by, victim = sorted(set(owners[shared]))[:2]
            before = approvals(review)
            review.decide(first, by, "exclude", [shared])
            new, messages = replan(world, review)
            self.assertFalse(any(shared in u["chosen"] for u in new["units"]), seed)  # EXCLUDE never reuses it
            decision = review.units[victim]
            self.assertEqual(rv.user_state(decision), rv.INVALIDATED, seed)
            conflict = review.conflicts[victim]
            self.assertEqual(conflict["cause"], "excluded")
            self.assertEqual(conflict["with"], by)
            self.assertIn(shared, conflict["shots"])
            self.assertTrue(conflict["options"])
            record = [r for r in review.records if r["type"] == "INVALIDATED" and r["speech_ref"] == victim][-1]
            self.assertEqual(record["conflict"]["cause"], "excluded")
            self.assertIn(f"câu {by[1:]}", record["invalidation_reason"])
            for unit_id in set(before) - {by, victim} - {u for u, ids in before.items() if shared in ids}:
                self.assertEqual(approvals(review).get(unit_id), before[unit_id], (seed, unit_id))
            self.assertTrue(all(review.conflicts[u]["cause"] in REAL_CAUSES for u in review.conflicts))
            found += 1
        self.assertGreater(found, 3)

    def test_a_shot_marked_missing_never_becomes_an_ai_match(self):
        world = World(5)
        first = world.plan()
        review = approve_all(first)
        unit_id = first["units"][1]["unit_id"]
        review.decide(first, unit_id, "missing")
        self.assertNotIn(unit_id, review.edits().keep)
        new, _ = replan(world, review)
        self.assertEqual(rv.user_state(review.units[unit_id]), rv.USER_SOURCE_MISSING)
        self.assertNotIn(unit_id, rv.settled_by_user(new, rv.Review()))


class SourceChangeTests(unittest.TestCase):  # A3
    def test_a_new_plan_revision_alone_keeps_approvals(self):
        world = World(7)
        first = world.plan()
        review = approve_all(first)
        before = approvals(review)
        new, messages = replan(world, review, plan_id="p-later")
        self.assertEqual(messages, [])
        self.assertEqual(approvals(review), before)

    def test_changed_source_content_with_the_same_shot_ids_invalidates_the_approvals_on_it(self):
        world = World(7)
        first = world.plan()
        review = approve_all(first)
        world.identity["v1"] = "id-1-edited"  # the file was replaced: same cuts, other content
        new, messages = replan(world, review)
        on_v1 = {u["unit_id"] for u in first["units"] if any(c.startswith("v1c") for c in u["chosen"])}
        self.assertTrue(on_v1)
        for unit_id in on_v1:
            self.assertEqual(rv.user_state(review.units[unit_id]), rv.INVALIDATED)
            self.assertEqual(review.conflicts[unit_id]["cause"], "source_changed")
        for unit_id in {u["unit_id"] for u in first["units"]} - on_v1:
            self.assertEqual(rv.user_state(review.units[unit_id]), rv.USER_APPROVED, unit_id)
        self.assertEqual(len(messages), len(on_v1))

    def test_an_edited_sentence_invalidates_only_its_own_approval(self):
        world = World(9)
        first = world.plan()
        review = approve_all(first)
        text, ms = world.spoken[1]
        world.spoken[1] = (text.replace("rất đẹp", "rất bền"), ms)
        _, messages = replan(world, review)
        self.assertEqual(review.conflicts["u2"]["cause"], "sentence_changed")
        self.assertEqual(len(messages), 1, messages)

    def test_a_review_saved_before_pieces_and_basis_still_reads_and_keeps_its_shots(self):
        world = World(11)
        first = world.plan()
        review = approve_all(first)
        old = review.to_dict()
        for decision in old["units"].values():
            decision.pop("pieces", None)
            decision.pop("basis", None)
        legacy = rv.Review.from_dict(old)
        self.assertTrue(all(p[1] == -1 for ids in legacy.edits().keep.values() for p in ids))
        # Without the pieces the shots are kept wherever they can play: one replan may still move a shared shot.
        # Whatever it voids says why, and what survives gets its pieces, so the next replan is exact.
        new, messages = replan(world, legacy)
        self.assertEqual(len(messages), len(legacy.conflicts))
        for unit_id, decision in legacy.units.items():
            if decision.approved is not None and not decision.invalidated:
                self.assertEqual(decision.pieces, rv.pieces_of(next(u for u in new["units"] if u["unit_id"] == unit_id)))
                self.assertTrue(decision.basis)


class MultiPartTests(unittest.TestCase):  # A4
    def test_forward_only_parts_keep_their_approvals_when_one_part_is_edited(self):
        parts = [World(20 + k, forward_only=True) for k in range(3)]
        reviews, logs = [], []
        for world in parts:
            logs.append(world.plan())
            reviews.append(approve_all(logs[-1]))
        before = [approvals(r) for r in reviews]
        edited = 1
        first = logs[edited]
        unit = first["units"][0]
        reviews[edited].decide(first, unit["unit_id"], "exclude", unit["chosen"][:1])
        for index, (world, review) in enumerate(zip(parts, reviews)):
            if index != edited:  # only the edited part plans again; the others keep plan and approvals
                self.assertEqual(approvals(review), before[index])
                continue
            _, messages = replan(world, review)
            for unit_id, cause in ((u, review.conflicts[u]["cause"]) for u in review.conflicts):
                self.assertIn(cause, REAL_CAUSES, (unit_id, messages))


class TransactionalReplanTests(unittest.TestCase):
    def test_a_replan_that_fails_loses_no_decision_and_leaves_no_half_state(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            clip = root / "clip.mp4"
            clip.write_bytes(b"mp4")
            value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                     "variants": ["Nồi chiên không dầu dung tích lớn. Lòng nồi chống dính. Mua ngay hôm nay."],
                     "visuals": [str(clip)], "mastering": {"schema_version": 1, "level": "balanced"}}
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(value), encoding="utf-8")

            def ffmpeg(argv, cwd, cancelled):
                target = Path(argv[-1])
                (target if target.is_absolute() else Path(cwd, target)).write_bytes(b"mp4")
            info = scene.StreamInfo(20_000, 1080, 1920, 30, True)
            out = root / "out"

            def render():
                return job.run(job.load_spec(spec_path), out, speaker=lambda status: FakeSpeaker(), probe=lambda path: 20_000,
                               measure=lambda path: (1200, 1200), ffmpeg=Path("ffmpeg"), ffmpeg_runner=ffmpeg,
                               inspect=lambda path: info)
            with mock.patch.object(scene, "analyse", return_value=analysis(20_000, [2500, 6000, 13_000])):
                self.assertEqual(render()["state"], "COMPLETED")
                work = out / ".dubflow-work"
                log_path = work / "editorial-1.json"
                data = json.loads(log_path.read_text(encoding="utf-8"))
                review = approve_all(data)
                spare = [c["id"] for c in data["candidates"] if c["id"] not in data["units"][0]["chosen"]]
                review.decide(data, data["units"][0]["unit_id"], "pin", spare[:1])
                rv.save(rv.path_for(work, 1), review)
                saved_review = rv.path_for(work, 1).read_bytes()
                saved_log = log_path.read_bytes()
                with mock.patch.object(planner, "plan_editorial", side_effect=RuntimeError("planner crashed")):
                    failed = render()
                # mastering never stops the video: it is joined the usual way, said so, and never presented as reviewed
                mastering = failed["videos"][0]["mastering"]
                self.assertIn("Không lập được kế hoạch hậu kỳ", mastering["fallback"])
                self.assertTrue(mastering["needs_review"])
                self.assertEqual(rv.path_for(work, 1).read_bytes(), saved_review)  # every decision still there
                self.assertEqual(log_path.read_bytes(), saved_log)                  # no half-written log
                self.assertEqual(render()["state"], "COMPLETED")                    # retryable
                again = rv.load(rv.path_for(work, 1))
                for unit_id, decision in again.units.items():
                    if unit_id != data["units"][0]["unit_id"] and decision.approved is not None:
                        self.assertEqual(rv.user_state(decision), rv.USER_APPROVED, unit_id)


def is_shown_again(first: dict, new: dict, unit_id: str) -> bool:
    """The same pieces, only now marked as shown again."""
    old, now = pieces(first, unit_id), pieces(new, unit_id)
    strip = lambda ps: [(p["id"], p["offset_ms"], p["length_ms"]) for p in ps or ()]  # noqa: E731
    return bool(now) and strip(old) == strip(now) and any(p.get("repeat") for p in now)


def trial(seed: int, forward_only: bool) -> tuple[int, int, int]:
    """One randomized replan: approve everything, one pin or exclusion, plan again. Returns (approvals checked,
    avoidable invalidations, invalidations with a real cause, approvals kept over footage that changed)."""
    rnd = random.Random(seed * 7 + 1)
    world = World(seed, forward_only=forward_only)
    try:
        first = world.plan()
    except ValueError:
        return 0, 0, 0, 0
    review = approve_all(first)
    target = rnd.choice(first["units"])
    others = [c["id"] for c in first["candidates"] if c["id"] not in target["chosen"]]
    if rnd.random() < 0.5 and others:
        action, ids = "pin", [rnd.choice(others)]
    elif target["chosen"]:
        action, ids = "exclude", [rnd.choice(target["chosen"])]
    else:
        return 0, 0, 0, 0
    before = approvals(review)
    review.decide(first, target["unit_id"], action, ids)
    try:
        new, _ = replan(world, review)
    except ValueError:
        return 0, 0, 0, 0
    avoidable = real = silent = 0
    for unit in new["units"]:
        decision = review.units.get(unit["unit_id"])
        if unit["unit_id"] != target["unit_id"] and rv.user_state(decision) == rv.USER_APPROVED \
                and pieces(first, unit["unit_id"]) != unit.get("pieces"):
            silent += 1
    for unit_id, shown in before.items():
        if unit_id == target["unit_id"] or not review.units[unit_id].invalidated:
            continue
        cause = review.conflicts[unit_id]["cause"]
        touched = set(ids) & set(shown)
        if cause in ("excluded", "pinned_elsewhere") and touched:
            real += 1
        elif cause == "shown_again" and forward_only and is_shown_again(first, new, unit_id):
            real += 1  # forward only: the action's footage plays later in the source, the approved piece is now a repeat
        else:
            avoidable += 1
    return len(before), avoidable, real, silent


class RandomizedReplanningTests(unittest.TestCase):  # A5
    def test_no_avoidable_invalidation_over_randomized_replans(self):
        for forward_only in (False, True):
            totals = [0, 0, 0, 0]
            for seed in range(150):
                for i, n in enumerate(trial(seed, forward_only)):
                    totals[i] += n
            with self.subTest(forward_only=forward_only):
                self.assertGreater(totals[0], 400)
                self.assertEqual(totals[1], 0, totals)  # no avoidable invalidation
                self.assertEqual(totals[3], 0, totals)  # no silent change of approved footage
                self.assertGreater(totals[2], 0, totals)  # real conflicts happen, and each is recorded


class ReviewFollowUpTests(unittest.TestCase):
    """Findings of the independent review of #782, each with its reproduction."""

    def test_a_sentence_that_shows_one_shot_twice_keeps_its_approval(self):  # rc12 W5: voided as plan_changed
        import copy
        world = World(3)
        first = world.plan()
        unit = next(u for u in first["units"] if u["chosen"])
        shot = unit["chosen"][0]
        unit["chosen"] = [shot, *unit["chosen"]]  # the same shot again, as a forward-only part held it twice
        if unit.get("pieces"):
            unit["pieces"] = [dict(unit["pieces"][0]), *unit["pieces"]]
            unit["pieces"][1]["repeat"] = True
        review = rv.Review()
        review.decide(first, unit["unit_id"], "approve")
        self.assertEqual(review.units[unit["unit_id"]].approved, list(dict.fromkeys(unit["chosen"])))
        again = rv.Review.from_dict(json.loads(json.dumps(review.to_dict())))  # saved and loaded, as the desktop does
        self.assertEqual(again.reconcile(copy.deepcopy(first), run="run-2"), [])
        self.assertEqual(rv.user_state(again.units[unit["unit_id"]]), rv.USER_APPROVED)

    def test_an_approval_saved_by_rc12_on_a_shot_shown_twice_is_kept(self):
        """Up to rc12 the material half of an approval's basis hashed a repeated shot twice; read now (each shot
        once) it must not look like a changed source."""
        import copy
        world = World(3)
        first = world.plan()
        first["source_identity"] = "content"
        first["sources"] = {c["source_id"]: "a" * 64 for c in first["candidates"]}
        unit = next(u for u in first["units"] if u["chosen"])
        unit["chosen"] = [unit["chosen"][0], *unit["chosen"]]
        if unit.get("pieces"):
            unit["pieces"] = [dict(unit["pieces"][0]), *unit["pieces"]]
            unit["pieces"][1]["repeat"] = True
        review = rv.Review()
        review.decide(first, unit["unit_id"], "approve")
        saved = review.to_dict()
        old = rv.basis(unit, first, repeats=True)
        self.assertNotEqual(old, rv.basis(unit, first))  # the two ways differ for this sentence
        saved["units"][unit["unit_id"]]["approved"] = list(unit["chosen"])  # as rc12 saved it: with the repeat
        saved["units"][unit["unit_id"]]["basis"] = old
        again = rv.Review.from_dict(json.loads(json.dumps(saved)))
        self.assertEqual(again.reconcile(copy.deepcopy(first), run="run-2"), [])
        self.assertEqual(rv.user_state(again.units[unit["unit_id"]]), rv.USER_APPROVED)
        self.assertEqual(again.units[unit["unit_id"]].basis, rv.basis(unit, first))  # taken the new way from now on
        # a real change of source content is still a changed source
        changed = copy.deepcopy(first)
        changed["sources"] = {k: "b" * 64 for k in changed["sources"]}
        later = rv.Review.from_dict(json.loads(json.dumps(saved)))
        self.assertTrue(later.reconcile(changed, run="run-3"))
        self.assertEqual(later.conflicts[unit["unit_id"]]["cause"], "source_changed")

    def test_approving_one_sentence_alone_changes_no_other_sentence(self):
        changed = checked = 0
        for forward_only in (False, True):
            for seed in range(60):
                world = World(seed, forward_only=forward_only)
                try:
                    first = world.plan()
                except ValueError:
                    continue
                for unit in first["units"]:
                    if not unit["chosen"]:
                        continue
                    review = rv.Review()
                    review.decide(first, unit["unit_id"], "approve")
                    again = world.plan(review.edits())
                    checked += 1
                    changed += [u.get("pieces") for u in again["units"]] != [u.get("pieces") for u in first["units"]]
        self.assertGreater(checked, 300)
        self.assertEqual(changed, 0)  # was 469 forward-only and 312 reorder trials out of 729

    def test_a_log_without_content_identities_never_voids_approvals_as_source_changed(self):
        for seed in range(15):
            world = World(seed)
            first = world.plan()
            first.pop("source_identity")  # a log from before the content identity was written
            review = approve_all(first)
            target = first["units"][0]
            others = [c["id"] for c in first["candidates"] if not any(c["id"] in u["chosen"] for u in first["units"])]
            if not others:
                continue
            review.decide(first, target["unit_id"], "pin", [others[0]])
            replan(world, review)
            self.assertNotIn("source_changed", {c["cause"] for c in review.conflicts.values()}, seed)

    def test_the_source_identity_of_a_log_is_its_content_not_the_analysis_recipe(self):
        from engine.dubflow.mastering import plan as m
        with TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            clip.write_bytes(b"same bytes" * 100)
            self.assertNotEqual(m.file_identity(clip, "recipe-1"), m.file_identity(clip, "recipe-2"))
            self.assertEqual(m.source_content(clip), m.content_fingerprint(clip))
            # unreadable: unknown, never a stand-in (the cache identity holds the recipe)
            self.assertIsNone(m.source_content(Path(tmp) / "missing.mp4"))
            self.assertEqual(m.source_contents({"v1": clip, "v2": Path(tmp) / "missing.mp4"}), {"v1": m.content_fingerprint(clip)})

    def test_a_sentence_spoken_longer_or_shorter_is_timing_changed(self):
        causes: dict[str, int] = {}
        for factor in (1.3, 0.6):
            for seed in range(40):
                world = World(seed, forward_only=bool(seed % 2))
                try:
                    first = world.plan()
                except ValueError:
                    continue
                review = approve_all(first)
                world.spoken = [(text, int(ms * factor)) for text, ms in world.spoken]
                try:
                    replan(world, review)
                except ValueError:
                    continue
                for conflict in review.conflicts.values():
                    causes[conflict["cause"]] = causes.get(conflict["cause"], 0) + 1
        self.assertGreater(causes.get("timing_changed", 0), 50, causes)
        self.assertEqual(causes.get("plan_changed", 0), 0, causes)  # was 164 at ×1.3 and 78 at ×0.6

    def test_an_approved_piece_now_shown_again_is_never_silent(self):
        world = World(78, forward_only=True)
        first = world.plan()
        review = approve_all(first)
        review.decide(first, "u1", "pin", ["v1c2"])
        new, _ = replan(world, review)
        for unit in new["units"]:
            if unit["unit_id"] == "u1" or not any(p.get("repeat") for p in unit.get("pieces") or ()):
                continue
            decision = review.units[unit["unit_id"]]
            if decision.approved is not None:  # still approved: it was approved as a repeat
                self.assertEqual(decision.pieces, rv.pieces_of(unit))
            else:
                self.assertIn(review.conflicts[unit["unit_id"]]["cause"], REAL_CAUSES)

    def test_approvals_of_sentences_that_moved_never_steer_the_new_plan(self):
        for seed in range(30):
            world = World(seed)
            first = world.plan()
            review = approve_all(first)
            world.spoken.insert(0, ("Mở đầu bằng một câu mới.", 1800))  # every unit id now names another sentence
            stale = world.plan(review.edits())
            fresh = world.plan()
            self.assertEqual([u["pieces"] for u in stale["units"]], [u["pieces"] for u in fresh["units"]], seed)
            review.reconcile(stale, run="run-2")
            self.assertTrue(all(c["cause"] in ("sentence_changed", "sentence_removed") for c in review.conflicts.values()))

    def test_an_approved_shot_that_is_no_longer_usable_keeps_its_approval(self):
        dark = shots.Quality(10, 4, 2, ("tối",))
        pool = [shots.Candidate("v1c1", "v1", 0, 3000, 0, 40, dark, 1), shots.Candidate("v1c2", "v1", 3000, 6000, 1, 40, dark, 2)]
        found = ev.gather(c.candidate_id for c in pool)
        units = align.units([("Đây là nồi chiên.", 2000), ("Bạn thấy lòng nồi.", 2000)], 5000, lead_ms=300, gap_ms=250)
        _, decisions, _ = align.assign(units, pool, found, PACING, forward_only=False)
        log = {"units": [d.to_dict() for d in decisions], "candidates": [{**c.to_dict(), "id": c.candidate_id} for c in pool]}
        review = approve_all(log)
        bright = shots.Candidate("v2c1", "v2", 0, 6000, 0, 40, shots.Quality(120, 50, 300), 3)
        _, again, _ = align.assign(units, [*pool, bright], ev.gather(["v1c1", "v1c2", "v2c1"]), PACING, forward_only=False,
                                   edits=review.edits())  # was KeyError: ('u1', 'v1c...')
        self.assertEqual([d.chosen for d in again], [d.chosen for d in decisions])


if __name__ == "__main__":
    unittest.main()

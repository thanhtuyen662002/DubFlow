"""Gate B, long-form evidence (P2-N11): the vision budget goes to the footage a programme uses, every part in turn.

Reproduces rc8 F (20 items, each a 105 s window of the same 36-minute film): candidates were spread over the whole
film (12 inside an item's window), the first look spread 54 frames over all 20 copies of the film (7 inside the
windows) and the first parts took what was left, so 16/23 parts were signals_only and 193/214 sentences had no
match. Unanalysed footage is never SOURCE_INSUFFICIENT.
"""
from __future__ import annotations

from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import review as rv
from engine.dubflow.mastering import vision as seeing
from engine.dubflow.mastering.planners import compilation as planner

FILM_MS = 2_146_800
WINDOW_MS = 105_000


def film() -> scene.Analysis:
    """A long film cut every 2.4 s (about 900 shots, more than the candidate cap)."""
    cuts = tuple(range(2400, FILM_MS, 2400))
    bounds = (0, *cuts, FILM_MS)
    return scene.Analysis(FILM_MS, cuts, tuple(scene.Shot(a, b, 30) for a, b in zip(bounds, bounds[1:])), (), True, "film")


def copies(n: int = 20) -> tuple[dict[str, list[shots.Candidate]], dict[str, tuple[int, int]], dict[str, str]]:
    """``n`` sources that are the same film (hard links), each trimmed to its own window, with candidates as the
    extraction makes them; ``within`` decides whether extraction knows the trim."""
    rnd = random.Random(1)
    hashes = {}
    found: dict[str, list[shots.Candidate]] = {}
    trims = {}
    for k in range(n):
        source = f"i{k + 1}-s1"
        trims[source] = (k * WINDOW_MS, (k + 1) * WINDOW_MS)
        found[source] = []
        for number, (start, end, shot, motion) in shots.numbered(film(), trims[source]):
            dhash = hashes.setdefault((start, end), rnd.getrandbits(64))  # the same picture in every copy
            found[source].append(shots.Candidate(f"{source}c{number}", source, start, end, shot, motion,
                                                 shots.Quality(120, 50, 300), dhash, f"{source}-{number}.jpg"))
    return found, trims, shots.duplicate_groups([c for mine in found.values() for c in mine])


class TrimmedCandidatesTests(unittest.TestCase):
    def test_a_window_of_a_long_film_gets_every_shot_of_the_window(self):
        whole = shots.windows(film())
        self.assertEqual(len(whole), shots.MAX_CANDIDATES)  # the cap spread over 36 minutes
        before = [w for w in whole if w[1] > 0 and w[0] < WINDOW_MS]
        within = shots.windows(film(), (0, WINDOW_MS))
        self.assertLess(len(before), 25)                 # rc8 F: 12 candidates for a 105 s item
        self.assertGreaterEqual(len(within), 43)         # every 2.4 s shot of the window
        covered = sum(min(b, WINDOW_MS) - max(a, 0) for a, b, *_ in within)
        self.assertGreaterEqual(covered, WINDOW_MS - 44 * 2 * shots.CUT_MARGIN_MS)

    def test_a_short_source_has_the_same_candidates_with_or_without_a_trim(self):
        short = scene.Analysis(60_000, (20_000,), (scene.Shot(0, 20_000, 10), scene.Shot(20_000, 60_000, 10)), (), True, "s")
        self.assertEqual(shots.windows(short), shots.windows(short, None))
        self.assertLess(len(shots.windows(short)), shots.MAX_CANDIDATES)


class CandidateIdTests(unittest.TestCase):  # review of #784: ids inside a trim, the cap boundary
    def test_a_shot_keeps_its_id_when_the_trim_moves(self):
        first = dict((w[:2], n) for n, w in shots.numbered(film(), (0, WINDOW_MS)))
        moved = dict((w[:2], n) for n, w in shots.numbered(film(), (30_000, 30_000 + WINDOW_MS)))
        both = set(first) & set(moved)
        self.assertGreater(len(both), 20)
        self.assertEqual({k: first[k] for k in both}, {k: moved[k] for k in both})
        # before: numbered from 1 inside each trim, so the shot at 31.2 s was c14 in one trim and c1 in the other
        self.assertTrue(all(n > shots.WITHIN_BASE for n in [*first.values(), *moved.values()]))

    def test_an_id_of_the_whole_source_spread_never_names_a_shot_of_a_trim(self):
        whole = {n for n, _ in shots.numbered(film())}
        self.assertEqual(whole, set(range(1, shots.MAX_CANDIDATES + 1)))
        self.assertFalse(whole & {n for n, _ in shots.numbered(film(), (0, WINDOW_MS))})

    def extract(self, found, within):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            video = Path(folder) / "clip.mp4"
            video.write_bytes(b"clip" * 256)

            def run(argv, cwd, cancelled):
                raise OSError("no FFmpeg here")  # unreadable frames: the ids are what is tested
            made = shots.extract(video, "s1", found, Path(folder) / "cache", ffmpeg=Path("ffmpeg"), run=run, within=within)
            return [(c.candidate_id, c.start_ms, c.end_ms) for c in made], sorted(p.name for p in (Path(folder) / "cache").glob("*.json"))

    def test_a_source_with_exactly_the_cap_of_windows_is_the_same_with_or_without_a_trim(self):
        length = shots.MAX_CANDIDATES * 2400
        bounds = range(0, length + 1, 2400)
        exact = scene.Analysis(length, tuple(bounds[1:-1]), tuple(scene.Shot(a, b, 10) for a, b in zip(bounds, bounds[1:])),
                               (), True, "exact")
        self.assertEqual(len(shots.windows(exact)), shots.MAX_CANDIDATES)
        self.assertEqual(self.extract(exact, (0, 60_000)), self.extract(exact, None))  # was: trimmed, another cache

    def test_a_long_source_inside_a_trim_has_its_own_cache_and_stable_ids(self):
        trimmed, cache = self.extract(film(), (0, WINDOW_MS))
        whole, whole_cache = self.extract(film(), None)
        self.assertNotEqual(cache, whole_cache)
        self.assertTrue(all(int(cid.rpartition("c")[2]) > shots.WITHIN_BASE for cid, _, _ in trimmed))
        self.assertGreaterEqual(len(trimmed), 43)


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.found, self.trims, self.groups = copies()
        self.parts = [planner.inside_trim(self.found[s], self.trims[s]) for s in sorted(self.found, key=lambda s: int(s[1:-3]))]

    def test_every_part_is_looked_at_inside_its_window_before_any_part_twice(self):
        room = 80 - 80 // 3
        picked = seeing.allocate(self.parts, Path("f"), room, self.groups)
        self.assertEqual(len(picked), room)
        inside = {c.candidate_id for part in self.parts for c in part}
        self.assertTrue(all(cid in inside for cid, _ in picked))
        per_part = [sum(1 for cid, _ in picked if cid.startswith(f"i{k + 1}-")) for k in range(20)]
        self.assertTrue(all(n >= room // 20 for n in per_part), per_part)
        self.assertLessEqual(max(per_part) - min(per_part), 1)

    def test_the_old_broad_look_misses_most_windows(self):  # the reproduction, kept as the baseline
        everything = [c for mine in shots_whole().values() for c in mine]
        first = seeing.broad(everything, Path("f"), 53)
        inside = 0
        for cid, _ in first:
            source = cid.split("c")[0]
            c = next(x for x in everything if x.candidate_id == cid)
            a, b = self.trims[source]
            inside += c.end_ms > a and c.start_ms < b
        self.assertLess(inside, 10)

    def test_one_picture_is_looked_at_once_and_its_answer_shared(self):
        pool = [c for part in self.parts for c in part]
        a = pool[0]
        twin = shots.Candidate("x9c1", "x9", a.start_ms, a.end_ms, 0, 30, a.quality, a.dhash, "x9-1.jpg")
        groups = shots.duplicate_groups(pool + [twin])
        picked = seeing.allocate([[a], [twin]], Path("f"), 5, groups)
        self.assertEqual(len(picked), 1)
        seen = seeing.Seen("một người lính", ("soldier",), (), "", (), 900, "clear", None, None, 0)
        shared = seeing.share({a.candidate_id: seen}, groups, pool + [twin])
        self.assertIs(shared["x9c1"], seen)

    def test_one_frame_with_many_duplicates_is_one_frame_of_the_budget(self):  # lead review #784, finding 2
        a = self.parts[0][0]
        twins = [shots.Candidate(f"x{k}c1", f"x{k}", a.start_ms, a.end_ms, 0, 30, a.quality, a.dhash, f"x{k}-1.jpg")
                 for k in range(4)]
        pool = [c for part in self.parts for c in part] + twins
        groups = shots.duplicate_groups(pool)
        seen = seeing.Seen("một người lính", ("soldier",), (), "", (), 900, "clear", None, None, 0)

        class Describer:
            taken = 0

            def describe(self, frames, cancelled):
                self.taken += len(frames)
                return {candidate_id: seen for candidate_id, _ in frames}
        describer = Describer()
        result = seeing.look(describer, [(a.candidate_id, Path("f") / a.keyframe)], groups, pool)
        self.assertEqual(result.frames, 1)            # the model was paid for one frame
        self.assertEqual(result.shared, len(twins))   # four shots share its answer, counted apart
        self.assertEqual(len(result.found), 1 + len(twins))
        self.assertEqual(describer.taken, 1)

    def test_spread_order_covers_the_stretch_with_any_prefix(self):
        order = seeing.spread_order(list(range(16)))
        self.assertEqual(sorted(order), list(range(16)))
        self.assertTrue(max(order[:4]) >= 8 and min(order[:4]) < 4)

    def test_spread_order_looks_at_the_middle_first_never_the_first_shot(self):  # review of #784
        for n in (3, 5, 16, 43, 100):
            order = seeing.spread_order(list(range(n)))
            self.assertEqual(sorted(order), list(range(n)), n)
            self.assertLessEqual(abs(order[0] - (n - 1) / 2), 1, n)  # was 0: a one-frame look saw the stretch's start
            self.assertNotEqual(order[0], 0, n)

    def test_a_shot_answered_through_its_duplicate_is_never_looked_at_again(self):  # review of #784
        one, _, _ = copies(1)
        # two items using the same window of the same film (hard links): every picture is in both
        found = {"i1-s1": one["i1-s1"], "i2-s1": [shots.Candidate(c.candidate_id.replace("i1-", "i2-"), "i2-s1", c.start_ms, c.end_ms,
                                                                 c.shot_index, c.motion, c.quality, c.dhash, c.keyframe)
                                                  for c in one["i1-s1"]]}
        pool = [c for mine in found.values() for c in mine]
        groups = shots.duplicate_groups(pool)
        first = seeing.allocate(list(found.values()), Path("frames"), 8, groups)

        class Describer:
            taken = 0

            def describe(self, frames, cancelled):
                self.taken += len(frames)
                return {cid: seeing.Seen("một cánh đồng", (), (), "", (), 900, "clear", None, None, 0) for cid, _ in frames}
        broad = seeing.look(Describer(), first, groups, pool)
        self.assertGreater(broad.shared, 0)
        looked = {cid for cid, _ in first} | broad.answered()
        units = [{"unit_id": f"u{k}", "text": "xe tăng tiến qua làng", "chosen": [],
                  "coverage": {"needs_review": True, "status": "none"}} for k in range(3)]
        evidence = ev.gather(c.candidate_id for c in pool)
        picked = seeing.targets(units, pool, evidence, looked, 40, forward_only=False)
        self.assertTrue(picked)
        self.assertFalse(set(picked) & broad.answered())
        # before: only the frames asked counted as looked at, so their duplicates were asked again
        again = seeing.targets(units, pool, evidence, {cid for cid, _ in first}, 40, forward_only=False)
        self.assertTrue(set(again) & (broad.answered() - {cid for cid, _ in first}))


def shots_whole() -> dict[str, list[shots.Candidate]]:
    """The candidates as rc8 made them: the whole film for every copy."""
    rnd = random.Random(1)
    out = {}
    for k in range(20):
        source = f"i{k + 1}-s1"
        out[source] = [shots.Candidate(f"{source}c{n}", source, a, b, sh, mo, shots.Quality(120, 50, 300), rnd.getrandbits(64),
                                       f"{source}-{n}.jpg") for n, (a, b, sh, mo) in enumerate(shots.windows(film()), 1)]
    return out


class BudgetTests(unittest.TestCase):
    def test_the_budget_follows_the_narration_when_none_was_set(self):
        self.assertEqual(seeing.auto_budget(80, 30), 80)
        self.assertEqual(seeing.auto_budget(80, 120), 240)
        self.assertEqual(seeing.auto_budget(80, 214), seeing.LONG_FORM_CAP)
        self.assertLessEqual(seeing.auto_budget(80, 10_000), seeing.LONG_FORM_CAP)

    def test_a_budget_the_spec_sets_is_kept(self):
        from engine.dubflow.compilation import program
        self.assertTrue(program._editorial({"vision_budget": 80}).budget_set)
        self.assertFalse(program._editorial({}).budget_set)
        # lead review #784, finding 1: the automatic budget and an explicit 80 plan differently, so they never share a part
        self.assertNotEqual(program._editorial({}).digest_input("i1"), program._editorial({"vision_budget": 80}).digest_input("i1"))
        self.assertEqual(program._editorial({"vision_budget": 80}).digest_input("i1"),
                         program._editorial({"vision_budget": 80}).digest_input("i1"))


class CoverageMapTests(unittest.TestCase):
    def test_the_map_says_what_has_evidence_and_where_nobody_looked(self):
        pool = [shots.Candidate(f"s1c{n}", "s1", n * 1000, n * 1000 + 1000, n, 0, shots.Quality(120, 50, 300), n) for n in range(10)]
        found = ev.gather(c.candidate_id for c in pool)
        for n in (0, 1, 5):
            found[f"s1c{n}"].add(ev.Item("vision", 900, "xe tăng", ()))
        made = seeing.coverage_map(pool, found)
        self.assertEqual((made["shots"], made["shots_with_evidence"], made["milli"]), (10, 3, 300))
        self.assertEqual(made["unseen"][0], ["s1", 6000, 10_000])
        self.assertEqual(made["unseen"][1], ["s1", 2000, 5000])


class FallbackTests(unittest.TestCase):
    def test_footage_nobody_looked_at_is_never_source_insufficient(self):
        log = {"mode": "content", "candidates": [
            {"id": "c1", "quality": {"problems": []}, "evidence": {"items": [{"origin": "vision", "text": "đồng ruộng", "labels": []}]}},
            {"id": "c2", "quality": {"problems": []}, "evidence": {"items": []}}]}
        unit = {"unit_id": "u1", "function": "narration", "chosen": ["c1"],
                "coverage": {"matched_footage": 0, "status": "none", "needs_review": True, "milli": 0}}
        self.assertEqual(rv.evidence_state(unit, log), rv.MATCH_NOT_FOUND)
        log["candidates"][1]["evidence"]["items"].append({"origin": "vision", "text": "bầu trời", "labels": []})
        self.assertEqual(rv.evidence_state(unit, log), rv.SOURCE_INSUFFICIENT)


if __name__ == "__main__":
    unittest.main()

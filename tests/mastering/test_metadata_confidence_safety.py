"""#842: a file name says what a file is called, never what a shot shows.

File-name metadata (origin ``metadata``, provenance ``filename_metadata``) helps find footage: it ranks candidates.
Before this fix the word weights (idf) and the common words that the confidence and the coverage use were built from
all evidence, file names included, so a file name on this shot or on another one changed how much a word SEEN on this
shot weighed: 595 -> 661 below, across ``REVIEW_BELOW``, NEEDS_REVIEW -> READY. The confidence and the coverage now
use weights built from what can verify the picture alone (vision, OCR, a user's note). File names still rank shots;
the threshold is unchanged. A shot that only a file name (its own or another shot's) brings into a sentence's matches
does not count towards the sentence's coverage and is looked at (Final Stack Integrity review: coverage leak).

Synthetic shots only: no media, no network, no model, no AI.
"""
from __future__ import annotations

import random
import unittest

from engine.dubflow.mastering import align, evidence as ev, candidates as shots, policy as rules, review as rv

PACING = rules.STYLE_PACING["natural"]
TEXT = "alpha and beta and gamma and delta and epsilon"
SEEN = ev.Item("vision", 900, "alpha", (), "AI")
NAME = ev.Item("metadata", 700, "beta_gamma_delta_epsilon.mp4", (), "tên file")
SAID = ev.Item("transcript", 850, "beta gamma delta epsilon", (), "phụ đề nguồn")


def _candidate(cid: str, start: int, end: int, problems: tuple[str, ...] = ()) -> shots.Candidate:
    return shots.Candidate(cid, "v1", start, end, 0, 50, shots.Quality(120, 50, 300, problems), random.Random(cid).getrandbits(64))


def _case(extra: dict[str, tuple[ev.Item, ...]] | None = None):
    # B is usable but blurred, so A (the one seen shot) is the choice whatever B's file name says
    pool = [_candidate("A", 0, 6000), _candidate("B", 6000, 12_000, ("blurred",)), _candidate("C", 12_000, 18_000, ("blurred",))]
    found = ev.gather(["A", "B", "C"])
    found["A"].add(SEEN)
    for key, items in (extra or {}).items():
        for item in items:
            found[key].add(item)
    return pool, found, align.Unit("u1", TEXT, 0, 4000, "narration")


def _plan(pool, found, unit):
    choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=False)
    mine = [c for c in choices if c.unit_id == "u1"]
    log = {"mode": mode, "candidates": [{"id": key, "evidence": value.to_dict()} for key, value in found.items()]}
    state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, log, [{"needs_review": c.needs_review} for c in mine])
    return state, mine, decision


def _views(pool, found, unit, key: str = "A") -> tuple[int, int]:
    scorer = align.Scorer(pool, found)
    candidate = next(c for c in pool if c.candidate_id == key)
    relevance = scorer.relevance(unit, candidate)[0]
    return scorer.content_relevance(unit, candidate, relevance), scorer.unspoken_relevance(unit, candidate, relevance)


class FileNamesNeverReweighWhatIsSeen(unittest.TestCase):
    def test_the_leads_case_595_is_not_661(self):
        # #842 as traced on #840: two shots, A seen ("alpha", 950), B blurred with only a file name
        pool = [_candidate("A", 0, 6000), _candidate("B", 6000, 12_000, ("blurred",))]
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        for where in (None, "A", "B"):
            found = ev.gather(["A", "B"])
            found["A"].add(ev.Item("vision", 950, "alpha", (), "AI"))
            if where:
                found[where].add(NAME)
            state, mine, _ = _plan(pool, found, unit)
            with self.subTest(file_name_on=where):
                self.assertEqual([(c.candidate.candidate_id, c.confidence) for c in mine], [("A", 595)])  # 7810fec: 661
                self.assertNotEqual(state, rv.READY)

    def test_baseline_weak_vision_is_reviewed(self):
        state, mine, _ = _plan(*_case())
        self.assertEqual([(c.candidate.candidate_id, c.confidence) for c in mine], [("A", 562)])
        self.assertNotEqual(state, rv.READY)

    def test_a_file_name_on_another_shot_changes_no_confidence_and_no_state(self):
        state, mine, decision = _plan(*_case({"B": (NAME,)}))
        self.assertEqual([(c.candidate.candidate_id, c.confidence) for c in mine], [("A", 562)])  # 7810fec: 616, READY
        self.assertNotEqual(state, rv.READY)
        self.assertEqual(decision.coverage["illustrated_ms"], _plan(*_case())[2].coverage["illustrated_ms"])

    def test_a_file_name_on_the_same_shot_changes_no_confidence_and_no_state(self):
        state, mine, _ = _plan(*_case({"A": (NAME,)}))
        self.assertEqual([(c.candidate.candidate_id, c.confidence) for c in mine], [("A", 562)])  # 7810fec: 616, READY
        self.assertNotEqual(state, rv.READY)

    def test_the_seen_relevance_of_a_shot_ignores_every_file_name(self):
        plain = _views(*_case())
        for where in ("A", "B"):
            with self.subTest(file_name_on=where):
                self.assertEqual(_views(*_case({where: (NAME,)}))[0], plain[0])

    def test_coverage_is_invariant_to_other_shots_file_names(self):
        plain = _views(*_case())
        names = ["beta_gamma_delta_epsilon.mp4", "alpha_alpha_alpha.mov", "beta.mp4", "epsilon_delta_final_v2.mp4",
                 "alpha_beta_gamma_delta_epsilon_zeta.mkv"]
        for b in names:
            for c in names:
                extra = {"B": (ev.Item("metadata", 700, b, (), "tên file"),), "C": (ev.Item("metadata", 700, c, (), "tên file"),)}
                with self.subTest(b=b, c=c):
                    self.assertEqual(_views(*_case(extra)), plain)  # confidence view and coverage view

    def test_a_shots_own_file_name_still_counts_for_its_coverage(self):
        # the coverage VIEW of a shot still reads its own file name (P2-14); what counts towards a sentence's coverage
        # is decided apart, on the verifying evidence (see CoverageNeverComesFromFileNames)
        seen, covered = _views(*_case({"A": (NAME,)}))
        self.assertEqual(seen, _views(*_case())[0])
        self.assertGreater(covered, _views(*_case())[1])

    def test_speech_and_file_names_on_other_shots_together_change_nothing_seen(self):
        plain = _views(*_case())
        self.assertEqual(_views(*_case({"B": (NAME,), "C": (SAID,)})), plain)
        state, mine, _ = _plan(*_case({"B": (NAME, SAID), "C": (NAME,)}))
        self.assertNotEqual(state, rv.READY)
        self.assertTrue(all(c.confidence == 562 for c in mine if c.candidate.candidate_id == "A"))


class FileNamesStillFindFootage(unittest.TestCase):
    def test_the_ranking_still_reads_the_file_name(self):
        pool = [_candidate("A", 0, 6000), _candidate("B", 6000, 12_000)]
        found = ev.gather(["A", "B"])
        found["A"].add(ev.Item("vision", 900, "omega", (), "AI"))
        found["B"].add(NAME)
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        scorer = align.Scorer(pool, found)
        self.assertGreater(scorer.relevance(unit, pool[1])[0], scorer.relevance(unit, pool[0])[0])

    def test_a_shot_known_only_by_its_file_name_is_never_ready(self):
        pool = [_candidate("A", 0, 6000), _candidate("B", 6000, 12_000)]
        found = ev.gather(["A", "B"])
        found["A"].add(ev.Item("vision", 900, "omega", (), "AI"))
        found["B"].add(NAME)
        state, mine, _ = _plan(pool, found, align.Unit("u1", TEXT, 0, 4000, "narration"))
        self.assertNotEqual(state, rv.READY)
        self.assertTrue(all(c.needs_review for c in mine if c.candidate.candidate_id == "B"))

    def test_a_job_whose_evidence_all_verifies_has_one_set_of_weights(self):
        pool, found, _ = _case()
        scorer = align.Scorer(pool, found)
        self.assertIs(scorer.quiet, scorer.stats)


if __name__ == "__main__":
    unittest.main()


class CoverageNeverComesFromFileNames(unittest.TestCase):
    """Vision fixed and confident on A (760); C is seen showing one word only, so the sentence is half covered. Only
    file names change. 7810fec/a3c97c9: C's own file name made C a match, coverage 500 -> 1000, C 661, READY."""

    @staticmethod
    def _plan(names: dict[str, str]):
        pool = [_candidate("A", 0, 3000), _candidate("B", 3000, 6000), _candidate("C", 6000, 12_000)]
        found = ev.gather(["A", "B", "C"])
        found["A"].add(ev.Item("vision", 950, "beta gamma delta epsilon", (), "AI"))
        found["C"].add(ev.Item("vision", 950, "alpha", (), "AI"))
        for key, name in names.items():
            found[key].add(ev.Item("metadata", 700, name, (), "tên file"))
        unit = align.Unit("u1", TEXT, 0, 6000, "narration")
        choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=False)
        mine = [c for c in choices if c.unit_id == "u1"]
        log = {"mode": mode, "candidates": [{"id": key, "evidence": value.to_dict()} for key, value in found.items()]}
        state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, log, [{"needs_review": c.needs_review} for c in mine])
        return state, decision, mine

    def test_baseline_is_half_covered_and_reviewed(self):
        state, decision, mine = self._plan({})
        self.assertEqual(decision.coverage["illustrated_ms"], 3000)
        self.assertEqual(state, rv.NEEDS_REVIEW)
        self.assertTrue(any(c.confidence >= align.REVIEW_BELOW for c in mine))  # the confidence is there, the coverage not

    def test_the_shots_own_file_name_does_not_cover_the_sentence(self):
        for name in ("beta_gamma_delta_epsilon.mp4", "alpha_beta_gamma_delta_epsilon.mp4"):
            state, decision, mine = self._plan({"C": name})
            with self.subTest(name=name):
                self.assertNotEqual(state, rv.READY)  # a3c97c9: READY, coverage 1000
                self.assertLessEqual(decision.coverage["illustrated_ms"], 3000)
                self.assertTrue(all(c.needs_review for c in mine if c.candidate.candidate_id == "C"))
                self.assertTrue(any("chỉ khớp tên file" in n for n in decision.notes))

    def test_another_shots_file_name_does_not_cover_the_sentence(self):
        for names in ({"B": "beta_gamma_delta_epsilon.mp4"}, {"B": "omega.mp4", "C": "alpha_beta_gamma_delta_epsilon.mp4"},
                      {"A": "alpha_beta.mp4"}):
            state, decision, _ = self._plan(names)
            with self.subTest(names=names):
                self.assertNotEqual(state, rv.READY)
                self.assertLessEqual(decision.coverage["illustrated_ms"], 3000)  # a3c97c9: 6000 with B's name

    def test_no_file_name_anywhere_changes_nothing(self):
        state, decision, _ = self._plan({})
        self.assertNotIn("chỉ khớp tên file", " ".join(decision.notes))


class FileNamesNeverVerifyWithTheSameShotsAndPictures(unittest.TestCase):
    """The invariant (Owner directive, Stack Integrity review): with the same chosen shots and the same pictures, file
    names never make a sentence READY that the pictures alone do not. File names may change WHICH shots are chosen
    (ranking); a READY sentence must then be READY on what those shots are seen to show. Seeded sweep: 7810fec had 53
    such leaks over the full sweep, a3c97c9 6, this fix 0 (this test runs a slice of it)."""

    WORDS = [None, "alpha", "alpha beta", "alpha beta gamma", "omega", "beta gamma delta epsilon"]
    NAMES = [None, "beta_gamma_delta_epsilon.mp4", "alpha_beta_gamma.mp4", "alpha.mp4", "omega_zeta.mp4",
             "alpha_beta_gamma_delta_epsilon_tower_screen.mkv"]

    @staticmethod
    def _state(length: int, lengths, seen, said, names, usable=None, text: str = TEXT):
        pool, at = [], 0
        for key, part in zip("ABCD", lengths):
            pool.append(_candidate(key, at, at + part, () if usable is None or key in usable else ("blurred",)))
            at += part
        found = ev.gather([c.candidate_id for c in pool])
        for key, value in seen.items():
            if value:
                found[key].add(ev.Item("vision", value[1], value[0], (), "AI"))
        for key, value in said.items():
            if value:
                found[key].add(ev.Item("transcript", 850, value, (), "phụ đề nguồn"))
        for key, value in names.items():
            if value:
                found[key].add(ev.Item("metadata", 700, value, (), "tên file"))
        unit = align.Unit("u1", text, 0, length, "narration")
        choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=False)
        mine = [c for c in choices if c.unit_id == "u1"]
        log = {"mode": mode, "candidates": [{"id": key, "evidence": value.to_dict()} for key, value in found.items()]}
        state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, log, [{"needs_review": c.needs_review} for c in mine])
        return state, {c.candidate.candidate_id for c in mine}

    # the six leaks a3c97c9 still had in the full sweep (Stack Integrity review), each READY there
    LEAKS = [
        (TEXT, 6000, (3000, 3000, 6000), {"B": ("beta gamma delta epsilon", 900), "C": ("alpha", 950)}, {"A": "zeta eta"},
         {"A": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv", "B": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv",
          "C": "beta_gamma_delta_epsilon.mp4"}),
        (TEXT, 6000, (3000, 3000, 6000), {"B": ("beta gamma delta epsilon", 900), "C": ("alpha", 950)}, {"A": "zeta eta"},
         {"A": "beta_gamma_delta_epsilon.mp4", "B": "alpha_beta_gamma.mp4", "C": "beta_gamma_delta_epsilon.mp4"}),
        (TEXT, 8000, (2000, 2000, 2000, 6000),
         {"A": ("beta gamma delta epsilon", 950), "C": ("alpha beta gamma", 950), "D": ("alpha beta", 800)}, {"A": "zeta eta"},
         {"A": "omega_zeta.mp4", "B": "alpha_beta_gamma.mp4", "D": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv"}),
        (TEXT, 8000, (2000, 2000, 2000, 6000),
         {"A": ("omega", 950), "B": ("beta gamma delta epsilon", 800), "C": ("beta gamma delta epsilon", 700), "D": ("alpha", 950)},
         {"A": "zeta eta", "B": "alpha beta gamma delta epsilon"},
         {"A": "beta_gamma_delta_epsilon.mp4", "B": "alpha_beta_gamma.mp4", "D": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv"}),
        (TEXT, 8000, (2000, 2000, 2000, 6000),
         {"A": ("omega", 950), "B": ("beta gamma delta epsilon", 800), "C": ("beta gamma delta epsilon", 700), "D": ("alpha", 950)},
         {"A": "zeta eta", "B": "alpha beta gamma delta epsilon"},
         {"A": "beta_gamma_delta_epsilon.mp4", "C": "beta_gamma_delta_epsilon.mp4", "D": "alpha.mp4"}),
        ("alpha beta gamma", 8000, (2000, 2000, 2000, 6000),
         {"A": ("beta gamma delta epsilon", 900), "C": ("beta gamma delta epsilon", 700), "D": ("alpha", 950)},
         {"A": "alpha beta gamma delta epsilon", "C": "alpha beta gamma delta epsilon"},
         {"A": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv", "C": "beta_gamma_delta_epsilon.mp4",
          "D": "alpha_beta_gamma_delta_epsilon_tower_screen.mkv"}),
    ]

    def test_the_leaks_found_on_a3c97c9_are_closed(self):
        for text, length, lengths, seen, said, names in self.LEAKS:
            state, chosen = self._state(length, lengths, seen, said, names, text=text)
            with self.subTest(names=names):
                if state == rv.READY:  # a3c97c9: READY on every one of them
                    alone, _ = self._state(length, lengths, seen, {}, {}, usable=chosen, text=text)
                    self.assertEqual(alone, rv.READY)

    def test_names_never_verify_what_the_same_shots_do_not_show(self):
        rng = random.Random(845)
        checked = 0
        for length, lengths in ((4000, (2500, 6000, 6000)), (6000, (3000, 3000, 6000)), (8000, (2000, 2000, 2000, 6000))):
            keys = "ABCD"[:len(lengths)]
            for _ in range(60):
                seen = {k: ((w, rng.choice((700, 800, 900, 950))) if (w := rng.choice(self.WORDS)) else None) for k in keys}
                said = {k: ("alpha beta gamma delta epsilon" if rng.random() < .2 else None) for k in keys}
                for _ in range(6):
                    names = {k: rng.choice(self.NAMES) for k in keys}
                    state, chosen = self._state(length, lengths, seen, said, names)
                    if state != rv.READY:
                        continue
                    checked += 1
                    # the same chosen shots (the others kept, unusable, so every weight stays), pictures only
                    alone, _ = self._state(length, lengths, seen, {}, {}, usable=chosen)
                    with self.subTest(seen=seen, said=said, names=names):
                        self.assertEqual(alone, rv.READY)
        self.assertGreater(checked, 10)  # the sweep did reach READY sentences

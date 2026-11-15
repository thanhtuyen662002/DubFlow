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

"""#893 (Phase 8 F1): a narrated figure that the source's own speech, played under the sentence, states otherwise.

Narrow on purpose: only the same sentence with another figure in the same place, from a cue whose time is played
under the sentence (its provenance). Never the general word comparison of #844. Synthetic shots only."""
from __future__ import annotations

import random
import unittest

from engine.dubflow.mastering import align, claims, evidence as ev, candidates as shots, final_qc, review as rv
from engine.dubflow.mastering import policy as rules

PACING = rules.STYLE_PACING["natural"]
SAID = "In 2015 the old tractor crosses the shallow river."
CUE = (910, 4590, "In 2021 the old tractor crosses the shallow river.")
VISION = ev.Item("vision", 900, "An old tractor crosses a shallow river.", (), "AI")


def heard(text, cues, start=0, end=6000):
    return claims.heard_conflicts(text, [("s1", start, end, cues)])


class HeardConflictTests(unittest.TestCase):
    def test_the_phase7_fixture_year_is_a_conflict_with_its_cue(self):
        found = heard(SAID, [CUE])
        self.assertEqual([(f["label"], f["heard"], f["cue_ms"]) for f in found], [("năm 2015", "2021", [910, 4590])])
        self.assertEqual(found[0]["said"], CUE[2])

    def test_other_figures_dates_and_vietnamese_are_read_the_same_way(self):
        self.assertEqual([f["label"] for f in heard("Ngày 22/6/1941 quân Đức vượt biên giới phía tây.",
                                                    [(0, 3000, "Ngày 23/6/1941 quân Đức vượt biên giới phía tây.")])], ["22"])
        self.assertEqual([f["label"] for f in heard("Con đập cao 289 mét và dài hơn hai cây số.",
                                                    [(0, 3000, "Con đập cao 298 mét và dài hơn hai cây số.")])], ["289 mét"])
        # a narration that quotes part of a longer cue
        self.assertEqual([f["label"] for f in heard("the old tractor crossed the river in 1998",
                                                    [(0, 3000, "Everyone here remembers: the old tractor crossed the river in 1989, right?")])],
                         ["năm 1998"])

    def test_no_conflict_when_nothing_ties_the_cue_to_the_sentence(self):
        cases = [
            (SAID, (910, 4590, SAID)),                                                     # same figure
            (SAID, (910, 4590, "In 2021 we painted it red again.")),                     # another sentence, another year
            (SAID, (910, 4590, "Năm 2021 chiếc máy kéo cũ băng qua con sông cạn.")),       # another language
            ("It carried 1.500 people across the river that night.",
             (0, 3000, "It carried 1,500 people across the river that night.")),          # the same figure written otherwise
            ("It carried 1 500 people across the river that night.",
             (0, 3000, "It carried 1500 people across the river that night.")),           # spaced groups: not read as two
            ("In 2015 the tractor", (0, 3000, "In 2021 the tractor")),                     # too few words to be the same sentence
            ("The old tractor crosses the shallow river.", CUE),                          # the sentence states no figure
            (SAID, (910, 4590, "In 2021 the old red tractor crosses the shallow river.")),  # a word between: not the same
        ]
        for text, cue in cases:
            with self.subTest(text=text, cue=cue[2]):
                self.assertEqual(heard(text, [cue]), [])

    def test_a_cue_not_played_under_the_sentence_is_never_read(self):
        self.assertEqual(heard(SAID, [CUE], start=5000, end=9000), [])
        self.assertEqual(heard(SAID, [CUE], start=0, end=910), [])  # ends where the cue starts
        self.assertEqual(len(heard(SAID, [CUE], start=4589, end=9000)), 1)

    def test_a_heard_conflict_is_contradicted_even_in_a_sentence_about_nothing_displayed(self):
        signal = {"display": False, "verified": [], "unverified": ["năm 2015"], "conflicts": [], "heard_conflicts": ["năm 2015"]}
        self.assertEqual(claims.statuses(signal), [("năm 2015", claims.CONTRADICTED)])
        self.assertEqual(claims.unresolved(signal), ["năm 2015"])
        without = {"display": False, "verified": [], "unverified": ["năm 2015"], "conflicts": []}
        self.assertEqual(claims.statuses(without), [("năm 2015", claims.NOT_APPLICABLE)])  # unchanged without one


def _candidate(cid, start, end):
    return shots.Candidate(cid, "v1", start, end, 0, 50, shots.Quality(120, 50, 300, ()), random.Random(cid).getrandbits(64))


def _plan(cue):
    pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
    found = ev.gather(["v1c1", "v1c2"])
    found["v1c1"].add(VISION)
    found["v1c1"].cues = (cue,)
    found["v1c1"].add(ev.Item("transcript", 850, cue[2], (), "phụ đề nguồn"))
    unit = align.Unit("u1", SAID, 0, 4000, "narration")
    choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=False)
    return [c for c in choices if c.unit_id == "u1"], decision, mode, found


class PlannerTests(unittest.TestCase):
    def test_the_sentence_is_held_for_a_person_and_its_shot_is_unchanged(self):
        same, plain, _, _ = _plan((910, 4590, SAID))
        mine, decision, mode, found = _plan(CUE)
        self.assertEqual([c.candidate.candidate_id for c in mine], [c.candidate.candidate_id for c in same])  # no penalty
        self.assertEqual(decision.signals["claims"]["heard_conflicts"], ["năm 2015"])
        self.assertNotIn("heard_conflicts", plain.signals.get("claims") or {})
        self.assertTrue(decision.coverage["needs_review"])
        self.assertTrue(all(c.needs_review for c in mine))
        self.assertTrue(any("00:00:00,910–00:00:04,590" in n and "2021" in n for n in decision.notes), decision.notes)
        unit = {"unit_id": "u1", "coverage": decision.coverage, "notes": decision.notes, "signals": decision.signals}
        log = {"mode": mode, "candidates": [{"id": k, "evidence": v.to_dict()} for k, v in found.items()]}
        state = rv.unit_state(unit, log, [{"needs_review": c.needs_review} for c in mine])
        self.assertNotEqual(state, rv.READY)
        priority, reasons = rv.priority(unit, state, None, mode)
        self.assertEqual(priority, rv.CRITICAL)  # never bulk-accepted with "accept the unreviewed"
        self.assertTrue(any("phụ đề nguồn nói con số khác với lời câu: năm 2015" in r for r in reasons), reasons)


if __name__ == "__main__":
    unittest.main()

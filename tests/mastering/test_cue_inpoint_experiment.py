"""#867 (D1): the experimental cue in-point is off by default and, when on, moves only where a matched shot starts.

The scenarios are the hand-made engineering fixtures of ``tests/mastering/x2/d1_bench.py`` (ENGINEERING_FIXTURE_ONLY):
they test the mechanism, never the quality of real footage.
"""
from __future__ import annotations

import inspect
import unittest

from engine.dubflow.mastering import align, evidence as ev
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering.planners import compilation, product
from tests.mastering.x2 import d1_bench as bench


def _by_name(name: str) -> bench.Scenario:
    return next(s for s in bench.scenarios() if s.name == name)


def _plan(scenario: bench.Scenario, forward_only: bool, **kwargs):
    candidates, found = bench._evidence(scenario)
    spoken = [(s.text, scenario.sentence_ms) for s in scenario.sentences]
    units = align.units(spoken, sum(ms for _, ms in spoken), lead_ms=0, gap_ms=0,
                        function=None if forward_only else align.product_function)
    return align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=forward_only, **kwargs)


def _dump(planned):
    choices, decisions, mode = planned
    return ([(c.unit_id, c.candidate.candidate_id, c.offset_ms, c.length_ms, c.score, c.confidence, c.needs_review, c.reasons)
             for c in choices], [d.to_dict() for d in decisions], mode)


class OffByDefault(unittest.TestCase):
    def test_every_production_entry_point_defaults_to_off(self) -> None:
        for function in (align.assign, compilation.plan_part):
            self.assertIsNone(inspect.signature(function).parameters["cue_inpoint_lead_ms"].default, function.__name__)
        # the product planner never passes it
        self.assertNotIn("cue_inpoint", inspect.getsource(product))

    def test_off_is_the_plan_without_the_argument(self) -> None:
        for scenario in bench.scenarios():
            for forward_only in (False, True):
                with self.subTest(scenario=scenario.name, forward_only=forward_only):
                    self.assertEqual(_dump(_plan(scenario, forward_only)),
                                     _dump(_plan(scenario, forward_only, cue_inpoint_lead_ms=None)))

    def test_cue_times_are_not_logged(self) -> None:
        _, found = bench._evidence(_by_name("sync"))
        self.assertTrue(found["s0"].cues)
        self.assertNotIn("cues", found["s0"].to_dict())

    def test_invalid_lead_is_refused(self) -> None:
        for bad in (-1, 5001, 1.5, True, "300"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _plan(_by_name("sync"), False, cue_inpoint_lead_ms=bad)


class WhenOn(unittest.TestCase):
    def test_matched_shot_plays_over_its_cue(self) -> None:
        result = bench.compare(_by_name("sync"), False)
        self.assertEqual([u["played_over_cue"] for u in result["off"]["units"]], [False, False, True, False])
        self.assertEqual([u["played_over_cue"] for u in result["on"]["units"]], [True, True, True, True])
        self.assertEqual(result["on"]["units"][0]["played"], [("s0", 3000, 6000)])
        self.assertIn("thử nghiệm #867: điểm vào theo lời nguồn", _plan(_by_name("sync"), False, cue_inpoint_lead_ms=300)[0][0].reasons)

    def test_only_the_in_point_moves_inside_the_shot(self) -> None:
        for scenario in bench.scenarios():
            for forward_only in (False, True):
                with self.subTest(scenario=scenario.name, forward_only=forward_only):
                    on = _plan(scenario, forward_only, cue_inpoint_lead_ms=300)
                    off = _plan(scenario, forward_only)
                    for choice in on[0]:
                        self.assertGreaterEqual(choice.offset_ms, choice.candidate.start_ms)
                        self.assertLessEqual(choice.offset_ms + choice.length_ms, choice.candidate.end_ms)
                    self.assertEqual(sum(c.length_ms for c in on[0]), sum(c.length_ms for c in off[0]))

    def test_review_contract_unchanged(self) -> None:
        result = bench.run()
        self.assertEqual(result["summary"]["review_state_changed"], [])
        self.assertEqual(result["summary"]["on"]["ready"], result["summary"]["off"]["ready"])
        self.assertEqual(result["summary"]["on"]["no_footage_assigned"], result["summary"]["off"]["no_footage_assigned"])
        self.assertEqual(result["summary"]["duration_changed"], [])

    def test_no_new_timeline_problem(self) -> None:
        # forward only, skipping ahead in a shot must not leave a later sentence without footage (hard_negatives/fwd
        # went back to an earlier shot before the guard)
        self.assertEqual(bench.run()["summary"]["new_timeline_problems"], {})

    def test_shot_seen_to_match_keeps_its_in_point(self) -> None:
        result = bench.compare(_by_name("picture_before_speech_seen"), False)
        self.assertEqual(result["on"]["units"][0]["played"], result["off"]["units"][0]["played"])

    def test_known_harm_is_reported_not_hidden(self) -> None:
        # one shot that changes picture inside it (a pan): the line is said over the second half, so the in-point
        # moves away from the right picture. The bench must keep reporting it.
        self.assertEqual(bench.run()["summary"]["picture_worse"],
                         ["picture_before_speech:u1", "picture_before_speech/fwd:u1"])

    def test_no_cue_no_change(self) -> None:
        result = bench.compare(_by_name("no_cue"), True)
        self.assertEqual(result["on"]["units"], result["off"]["units"])

    def test_cue_choice_prefers_most_shared_words(self) -> None:
        on = bench.compare(_by_name("close_cues"), False)["on"]["units"][0]
        self.assertEqual(on["played"][0][1], 2600 - 300)

    def test_cue_at_end_of_source_stays_inside(self) -> None:
        on = bench.compare(_by_name("cue_at_edges"), False)["on"]["units"][1]
        self.assertEqual(on["played"], [("s1", 9000, 12000)])

    def test_terms_helper_ignores_function_words(self) -> None:
        self.assertFalse(ev.terms("the of and") & ev.terms("The bridge sways."))


if __name__ == "__main__":
    unittest.main()

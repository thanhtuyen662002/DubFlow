"""Cue lineage experiment in the planner: off by default, verified provenance, never picture evidence.

Fixture: ``tests/mastering/x2/cue_lineage.py`` (ENGINEERING_FIXTURE_ONLY; English speech, Vietnamese sentences written
by the agent). It tests the mechanism, never the quality of real footage.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from engine.dubflow.mastering import align, lineage as lin
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering.planners import compilation, product
from tests.mastering.x2 import cue_lineage as cl


def _setup():
    fx = cl.fixture()
    candidates = cl._candidates(fx)
    found = cl._evidence(fx, candidates, cl.indexes_of(fx), speech=True)
    units = align.units([(s.text, fx.sentence_ms) for s in fx.sentences], fx.sentence_ms * len(fx.sentences),
                        lead_ms=0, gap_ms=0, function=None)
    indexes = {sid: lin.CueIndex.from_document(sid, doc, "0" * 64) for sid, doc in fx.cue_docs.items()}
    return fx, candidates, found, units, indexes


def _dump(planned):
    choices, decisions, mode = planned
    return ([(c.unit_id, c.candidate.candidate_id, c.offset_ms, c.length_ms, c.confidence, c.needs_review, c.reasons)
             for c in choices], [d.to_dict() for d in decisions], mode)


class OffByDefault(unittest.TestCase):
    def test_every_entry_point_defaults_to_off(self) -> None:
        for function in (align.assign, compilation.plan_part):
            self.assertIsNone(inspect.signature(function).parameters["lineage"].default, function.__name__)
        self.assertNotIn("lineage", inspect.getsource(product))

    def test_off_is_the_plan_without_the_argument(self) -> None:
        _, candidates, found, units, _ = _setup()
        for forward_only in (False, True):
            plain = align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=forward_only)
            off = align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=forward_only, lineage=None)
            self.assertEqual(_dump(plain), _dump(off))
            self.assertTrue(all("lineage" not in d for d in _dump(off)[1]))

    def test_old_narration_without_cue_refs_ranks_as_today(self) -> None:
        _, candidates, found, units, indexes = _setup()
        none = lin.resolve("ranked", [None] * len(units), indexes)
        self.assertEqual({none.reason(i) for i in range(len(units))}, {"no_cue_written"})
        plain = _dump(align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=False))
        ranked = _dump(align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=False, lineage=none))
        self.assertEqual(plain[0], ranked[0])  # the same shots, offsets, confidence and flags

    def test_lineage_must_cover_every_sentence(self) -> None:
        _, candidates, found, units, indexes = _setup()
        with self.assertRaises(ValueError):
            align.assign(units, candidates, found, rules.STYLE_PACING["natural"], forward_only=False,
                         lineage=lin.resolve("region", [None], indexes))


class Provenance(unittest.TestCase):
    def test_cue_file_hash_is_checked(self) -> None:
        doc = cl.fixture().cue_docs["en"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "en.cues.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(lin.CueIndex.load("en", path, digest).sha256, digest)
            path.write_text(json.dumps(doc).replace("2023", "2024"), encoding="utf-8")
            with self.assertRaises(lin.LineageError):
                lin.CueIndex.load("en", path, digest)

    def test_rejections_carry_their_reason(self) -> None:
        *_, indexes = _setup()
        plan = lin.resolve("region", [["en:c1", "en:c1", "xx:c1", "en:c99", "bad", ""], None], indexes)
        self.assertEqual([q.cue_id for q in plan.cues[0]], ["c1"])
        self.assertEqual(plan.rejected[0], (("xx:c1", lin.REJECT_UNKNOWN_SOURCE), ("en:c99", lin.REJECT_UNKNOWN_CUE),
                                            ("bad", lin.REJECT_MALFORMED), ("", lin.REJECT_MALFORMED)))
        self.assertIsNone(plan.reason(0))
        self.assertEqual(plan.reason(1), "no_cue_written")
        self.assertEqual(lin.resolve("region", [["en:c99"]], indexes).reason(0), "cue_rejected")

    def test_times_come_from_the_index_only(self) -> None:
        *_, indexes = _setup()
        cue = lin.resolve("ranked", [["en:c1"]], indexes).cues[0][0]
        self.assertEqual((cue.source_id, cue.start_ms, cue.end_ms), ("en", 3500, 5200))

    def test_bad_mode_or_window_is_refused(self) -> None:
        *_, indexes = _setup()
        for kwargs in ({"mode": "both"}, {"mode": "region", "window_ms": -1}, {"mode": "region", "window_ms": 60_001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(lin.LineageError):
                lin.resolve(kwargs["mode"], [None], indexes, window_ms=kwargs.get("window_ms", 0))


class WhenOn(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runs = {(mode, fwd): cl.run_engine(mode, forward_only=fwd) for mode in lin.MODES for fwd in (False, True)}

    def units(self, mode: str, forward: bool = False) -> dict:
        return {u["uid"]: u for u in self.runs[(mode, forward)]["units"]}

    def test_mode_is_logged_and_picture_never_verified(self) -> None:
        for (mode, _), run in self.runs.items():
            for u in run["units"]:
                self.assertEqual(u["lineage"]["mode"], mode)
                self.assertIs(u["lineage"]["picture_verified"], False)
                self.assertTrue(all(link["picture_verified"] is False for link in u["lineage"]["links"]))

    def test_never_ready_and_cue_matches_stay_reviewed(self) -> None:
        for key, run in self.runs.items():
            self.assertEqual(run["summary"]["ready"], 0, key)
            for u in run["units"]:
                if u["matched"]:
                    self.assertEqual(u["state"], "NEEDS_REVIEW", (key, u["uid"]))

    def test_lineage_finds_what_words_miss(self) -> None:
        for key, run in self.runs.items():
            self.assertGreaterEqual(run["summary"]["top1_right"], 6, key)  # lexical: 2-3 of 10 on this fixture

    def test_region_has_no_whole_source_fallback(self) -> None:
        for forward in (False, True):
            units = self.units("region", forward)
            for uid in ("v9", "v10", "h1", "h3", "h4", "h5"):
                self.assertFalse(units[uid]["matched"], uid)
                self.assertEqual(units[uid]["state"], "MATCH_NOT_FOUND", uid)
                self.assertTrue(any("no_cue_region" in n for n in units[uid]["notes"]), uid)
            self.assertEqual(units["v10"]["lineage"]["no_cue"], "cue_rejected")
            self.assertEqual(units["v10"]["lineage"]["rejected"], [["en:c99", lin.REJECT_UNKNOWN_CUE]])

    def test_ranked_without_cue_falls_back_to_words(self) -> None:
        # h4 shares a year with the presenter's line: ranked keeps today's (wrong) match, region does not
        self.assertTrue(self.units("ranked")["h4"]["matched"])
        self.assertFalse(self.units("region")["h4"]["matched"])

    def test_hard_negative_citing_a_real_cue_gets_footage_but_is_reviewed(self) -> None:
        for mode in lin.MODES:
            h2 = self.units(mode)["h2"]
            self.assertTrue(h2["matched"])
            self.assertEqual(h2["state"], "NEEDS_REVIEW")
            self.assertEqual(h2["picture_right_ms"], 0)

    def test_shared_cue_links_both_sentences(self) -> None:
        units = self.units("region")
        self.assertEqual([k["cue_id"] for k in units["v1"]["lineage"]["links"]], ["c1"])
        self.assertEqual([k["cue_id"] for k in units["v8"]["lineage"]["links"]], ["c1"])

    def test_montage_logs_each_cited_cue_in_order(self) -> None:
        v6 = self.units("region")["v6"]
        self.assertEqual([k["cue_id"] for k in v6["lineage"]["links"]], ["c6", "c7"])

    def test_right_year_wrong_picture_counts_as_wrong(self) -> None:
        v4 = self.units("region")["v4"]
        self.assertTrue(v4["cue_covered"])
        self.assertEqual(v4["picture_right_ms"], 0)

    def test_forward_only_timeline_is_clean(self) -> None:
        for mode in lin.MODES:
            self.assertEqual(self.runs[(mode, True)]["summary"]["timeline_problems"], [], mode)

    def test_reorder_only_declares_repeats(self) -> None:
        # 8 of the 10 shots are cited, so the sentences without footage share the 2 left: a declared repeat, never a
        # gap, an overlap or a backwards jump
        for mode in lin.MODES:
            self.assertTrue(set(self.runs[(mode, False)]["summary"]["timeline_problems"]) <= {"REPEAT"}, mode)


if __name__ == "__main__":
    unittest.main()

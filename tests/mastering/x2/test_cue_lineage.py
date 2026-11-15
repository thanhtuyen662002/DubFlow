"""Cue lineage prototype (ENGINEERING_FIXTURE_ONLY): provenance is exact, never invented, never picture evidence."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.mastering.x2 import cue_lineage as cl


class Provenance(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = cl.fixture()
        self.indexes = cl.indexes_of(self.fx)

    def test_cue_file_hash_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "en.cues.json"
            path.write_text(json.dumps(self.fx.cue_docs["en"]), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(len(cl.CueIndex.load("en", path, digest).cues), 8)
            path.write_text(json.dumps(self.fx.cue_docs["en"]).replace("tractor", "tracker"), encoding="utf-8")
            with self.assertRaises(cl.LineageError):
                cl.CueIndex.load("en", path, digest)

    def test_malformed_or_duplicate_cues_are_refused(self) -> None:
        for bad in ([{"id": "c1", "start_ms": 5, "end_ms": 5, "text": "x"}],
                    [{"id": "c1", "start_ms": 0, "end_ms": 5, "text": "x"}, {"id": "c1", "start_ms": 6, "end_ms": 9, "text": "y"}],
                    [{"id": "c1", "start_ms": "0", "end_ms": 5, "text": "x"}]):
            with self.subTest(bad=bad), self.assertRaises(cl.LineageError):
                cl.CueIndex.from_document("en", {"cues": bad}, "0" * 64)

    def test_unknown_cue_is_rejected_not_repaired(self) -> None:
        resolved = {r.sentence.uid: r for r in cl.resolve(self.fx.sentences, self.indexes)}
        self.assertEqual(resolved["v10"].rejected, ["en:c99"])
        self.assertEqual(resolved["v10"].cues, [])
        self.assertEqual(resolved["v10"].reason, "cue_rejected")
        self.assertEqual(resolved["v9"].reason, "no_cue_written")
        self.assertEqual([c.cue_id for c in resolved["v6"].cues], ["c6", "c7"])  # several cues, in order

    def test_times_come_only_from_the_cue_file(self) -> None:
        resolved = {r.sentence.uid: r for r in cl.resolve(self.fx.sentences, self.indexes)}
        cue = resolved["v1"].cues[0]
        self.assertEqual((cue.start_ms, cue.end_ms), (3500, 5200))
        self.assertFalse(hasattr(resolved["v1"].sentence, "start_ms"))

    def test_lineage_never_claims_the_picture(self) -> None:
        for arm in ("region", "ranked"):
            for unit in cl.run_arm(arm)["units"]:
                for link in unit["lineage"]:
                    self.assertIs(link["picture_verified"], False)


class Benchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.arms = {arm: cl.run_arm(arm) for arm in cl.ARMS}
        cls.forward = {arm: cl.run_arm(arm, forward_only=True) for arm in cl.ARMS}

    def summary(self, arm: str, forward: bool = False) -> dict:
        return (self.forward if forward else self.arms)[arm]["summary"]

    def test_transcript_or_cue_never_makes_a_sentence_ready(self) -> None:
        for arm in cl.ARMS:
            for forward in (False, True):
                self.assertEqual(self.summary(arm, forward)["ready"], 0, (arm, forward))

    def test_lineage_finds_what_lexical_matching_misses(self) -> None:
        self.assertEqual(self.summary("lexical")["top1_right"], 3)
        self.assertEqual(self.summary("region")["top1_right"], 7)
        self.assertEqual(self.summary("region")["cited_cue_covered"], 7)

    def test_region_has_no_whole_source_fallback(self) -> None:
        units = {u["uid"]: u for u in self.arms["region"]["units"]}
        for uid in ("v9", "v10", "h1", "h3", "h4", "h5"):
            self.assertEqual(units[uid]["played"], [], uid)
            self.assertEqual(units[uid]["state"], "MATCH_NOT_FOUND", uid)

    def test_a_wrong_citation_is_still_footage_and_stays_reviewed(self) -> None:
        # lineage trusts the citation: a hard negative that cites a real cue gets that shot. It is never READY.
        units = {u["uid"]: u for u in self.arms["region"]["units"]}
        self.assertTrue(units["h2"]["matched"])
        self.assertEqual(units["h2"]["state"], "NEEDS_REVIEW")
        self.assertEqual(self.summary("region")["no_footage_assigned_ids"], ["h2"])

    def test_right_year_wrong_picture_is_not_counted_as_right(self) -> None:
        units = {u["uid"]: u for u in self.arms["region"]["units"]}
        self.assertTrue(units["v4"]["cue_covered"])
        self.assertEqual(units["v4"]["picture_right_ms"], 0)
        self.assertEqual(units["v4"]["state"], "NEEDS_REVIEW")

    def test_block_ablation_assigns_every_hard_negative(self) -> None:
        # more text without the cue-sentence link is not provenance: every no-footage sentence gets footage
        self.assertEqual(self.summary("block")["no_footage_assigned"], 5)

    def test_no_new_timeline_problem(self) -> None:
        for arm in cl.ARMS:
            for forward in (False, True):
                self.assertEqual(self.summary(arm, forward)["timeline_problems"], [], (arm, forward))


if __name__ == "__main__":
    unittest.main()

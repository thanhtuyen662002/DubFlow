"""Tests for the SOLO engineering benchmark (ENGINEERING_FIXTURE_ONLY). The end-to-end run needs ffmpeg; it is
skipped without it. Nothing here is a dataset entry or a human label."""
import os, shutil, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solo_bench as sb  # noqa: E402


class Construction(unittest.TestCase):
    def test_every_cue_lies_inside_its_own_shot(self):
        for i, (start, end, _) in enumerate(sb.cues()):
            self.assertEqual((sb._segment(start), sb._segment(end - 1)), (i, i))

    def test_hard_negatives_have_no_footage_and_matches_name_their_shots(self):
        for u in sb.units():
            if u.kind in ("match", "montage"):
                self.assertTrue(u.shots)
            else:
                self.assertEqual(u.shots, ())
        self.assertTrue(sb.ENGINEERING_FIXTURE_ONLY)

    def test_the_thing_of_an_entity_negative_is_never_said(self):
        said = " ".join(t for _, _, t in sb.cues()).casefold()
        for u in sb.units():
            if u.kind == "entity":
                self.assertNotIn(u.text.split()[1].casefold(), said)


class CueAlignedSimulation(unittest.TestCase):
    def test_a_piece_moves_to_just_before_the_matching_cue_and_stays_inside_its_shot(self):
        shot = SimpleNamespace(start_ms=6000, end_ms=12000)
        said = [(9600, 11300, "the lighthouse flashes over the harbor")]
        moved = sb.cue_aligned([("c", 6000, 9000)], "The lighthouse flashes.", {"c": shot}, said)
        self.assertEqual(moved, [("c", 9000, 12000)])  # 9300 would run past the shot: clamped to end - length

    def test_without_a_shared_term_the_piece_stays(self):
        shot = SimpleNamespace(start_ms=0, end_ms=6000)
        moved = sb.cue_aligned([("c", 0, 3000)], "Unrelated words here.", {"c": shot}, [(3000, 4000, "glacier melts")])
        self.assertEqual(moved, [("c", 0, 3000)])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.with_cues = sb.run(Path(cls.tmp.name))["summary"]
        cls.no_cues = sb.run(Path(cls.tmp.name), with_cues=False)["summary"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_fixture_is_cut_where_it_was_built(self):
        self.assertEqual(self.with_cues["shots_detected"], sb.N_SHOTS)

    def test_transcript_evidence_never_makes_a_sentence_ready(self):
        self.assertEqual(self.with_cues["ready"], 0)
        self.assertEqual(self.no_cues["ready"], 0)

    def test_without_speech_nothing_claims_footage(self):
        self.assertEqual(self.no_cues["false_assignments"], 0)
        self.assertEqual(self.no_cues["whole_part_review"], {"MATCH_NOT_FOUND": len(sb.units())})

    def test_with_speech_retrieval_finds_the_shot_that_says_it(self):
        self.assertEqual(self.with_cues["match_top1_segment"], self.with_cues["match_units"])

    def test_final_qc_blocks_the_unreviewed_part(self):
        self.assertFalse(self.with_cues["final_qc"]["decisions"])


if __name__ == "__main__":
    unittest.main()

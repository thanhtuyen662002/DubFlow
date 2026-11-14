"""#836: a transcript says what the source *says*, never what a shot *shows*.

A transcript item (origin ``transcript``, "phụ đề nguồn") helps find footage: it ranks candidates, explains a choice
and points the targeted vision look at the shots the speech is about. It never verifies the picture: it adds nothing
to a choice's confidence, the time it is said over is not footage that illustrates the sentence, and so it can never
make a sentence READY on its own, lift a weak vision answer over ``align.REVIEW_BELOW`` or hide a vision answer that
shows something else. Text read off the frame (provenance ``ocr``) is seen, not said, and still verifies.

Synthetic shots only: no media, no network, no model, no AI. T1–T7 are the cases of the hotfix directive.
"""
from __future__ import annotations

from pathlib import Path
import random
import unittest

from engine.dubflow.mastering import align, analysis as scene, evidence as ev, candidates as shots, plan as m
from engine.dubflow.mastering import policy as rules, review as rv, vision as seeing
from engine.dubflow.mastering.planners import compilation as compiler

PACING = rules.STYLE_PACING["natural"]
TEXT = "Quả địa cầu xoay để lộ lãnh thổ rộng lớn."
SEEN_TEXT = "Bản đồ trên quả địa cầu, vùng lãnh thổ rộng lớn được vẽ viền."
WEAK_VISION = ev.Item("vision", 700, SEEN_TEXT, (), "AI")
STRONG_VISION = ev.Item("vision", 900, SEEN_TEXT, (), "AI")
OTHER_VISION = ev.Item("vision", 850, "Đám đông đứng trên quảng trường.", (), "AI")
SAID = ev.Item("transcript", 850, "quả địa cầu xoay, lãnh thổ rộng lớn của nước Nga", (), "phụ đề nguồn")


def _candidate(cid: str, start: int, end: int) -> shots.Candidate:
    return shots.Candidate(cid, "v1", start, end, 0, 50, shots.Quality(120, 50, 300, ()), random.Random(cid).getrandbits(64))


def _log(mode: str, found: dict[str, ev.Evidence]) -> dict:
    return {"mode": mode, "candidates": [{"id": key, "evidence": value.to_dict()} for key, value in found.items()]}


def _plan(pool: list[shots.Candidate], found: dict[str, ev.Evidence], text: str = TEXT, length: int = 4000):
    unit = align.Unit("u1", text, 0, length, "narration")
    choices, (decision,), mode = align.assign([unit], pool, found, PACING, forward_only=False)
    mine = [c for c in choices if c.unit_id == "u1"]
    state = rv.unit_state({"unit_id": "u1", "coverage": decision.coverage}, _log(mode, found),
                          [{"needs_review": c.needs_review} for c in mine])
    return state, mine, decision


def _one_shot(*items: ev.Item):
    pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
    found = ev.gather(["v1c1", "v1c2"])
    for item in items:
        found["v1c1"].add(item)
    state, mine, decision = _plan(pool, found)
    return state, min(c.confidence for c in mine), decision


class TranscriptNeverVerifiesThePicture(unittest.TestCase):
    def test_baseline_weak_vision_alone_is_reviewed(self):
        state, confidence, _ = _one_shot(WEAK_VISION)
        self.assertLess(confidence, align.REVIEW_BELOW)
        self.assertNotEqual(state, rv.READY)

    def test_t1_transcript_does_not_lift_weak_vision_to_ready(self):
        alone, before, _ = _one_shot(WEAK_VISION)
        state, after, _ = _one_shot(WEAK_VISION, SAID)
        self.assertNotEqual(state, rv.READY)  # 3ecaf7d: 560 -> 680, READY
        self.assertEqual(after, before)  # the speech adds nothing to the confidence

    def test_t1_the_sentence_says_why_it_is_not_ready(self):
        _, _, alone = _one_shot(WEAK_VISION)
        _, _, decision = _one_shot(WEAK_VISION, SAID)
        why = "giây cảnh có lời nói trong nguồn khớp câu nhưng bằng chứng hình ảnh còn yếu (độ tin cậy dưới 600): cần kiểm tra"
        self.assertTrue(any(why in note for note in decision.notes), decision.notes)  # Windows replay: notes were empty
        self.assertFalse(any(why in note for note in alone.notes))  # no speech, no such note

    def test_t2_transcript_only_is_never_visual_ready(self):
        state, confidence, decision = _one_shot(SAID)
        self.assertNotEqual(state, rv.READY)  # 3ecaf7d: READY at 680 on a shot nobody looked at
        self.assertLess(confidence, align.REVIEW_BELOW)
        # the time it is said over is not footage that illustrates the sentence, and the sentence says so
        self.assertEqual(decision.coverage["illustrated_ms"], 0)
        self.assertTrue(decision.coverage["needs_review"])
        self.assertTrue(any(note.startswith("4.0 giây cảnh chỉ khớp lời nói trong nguồn") for note in decision.notes),
                        decision.notes)

    def test_t3_transcript_never_overrides_unrelated_vision(self):
        state, confidence, decision = _one_shot(OTHER_VISION, SAID)
        self.assertNotEqual(state, rv.READY)  # 3ecaf7d: READY at 680 over a crowd
        self.assertLess(confidence, align.REVIEW_BELOW)
        self.assertEqual(decision.coverage["illustrated_ms"], 0)

    def test_t4_valid_vision_is_still_ready_with_or_without_a_transcript(self):
        alone, before, _ = _one_shot(STRONG_VISION)
        with_speech, after, decision = _one_shot(STRONG_VISION, SAID)
        self.assertEqual((alone, with_speech), (rv.READY, rv.READY))
        self.assertEqual(after, before)
        self.assertFalse([n for n in decision.notes if "lời nói trong nguồn" in n])

    def test_text_read_off_the_frame_is_seen_not_said(self):
        # a transcript item whose detail says it was read off the frame (provenance ocr) is visual evidence
        read = ev.Item("transcript", 900, SEEN_TEXT, (), "OCR chữ trên hình")
        self.assertEqual(ev.provenance(read.origin, read.detail), "ocr")
        self.assertTrue(ev.verifies(read))
        self.assertFalse(ev.verifies(SAID))
        state, confidence, _ = _one_shot(read)
        self.assertEqual(state, rv.READY)
        self.assertGreaterEqual(confidence, align.REVIEW_BELOW)

    def test_a_name_said_over_a_shot_is_not_a_name_shown_on_it(self):
        # proper names are matched as entities: the speech naming one must not reach the confidence that way either
        said = ev.Item("transcript", 850, "Henry Stimson phát biểu", (), "phụ đề nguồn")
        state, confidence, _ = _one_shot(OTHER_VISION, said)
        stated = "Henry Stimson phát biểu trước đám đông."
        pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
        found = ev.gather(["v1c1", "v1c2"])
        found["v1c1"].add(ev.Item("vision", 850, "Khung cảnh bến cảng vắng.", (), "AI"))
        found["v1c1"].add(said)
        state, mine, _ = _plan(pool, found, stated)
        self.assertNotEqual(state, rv.READY)
        self.assertTrue(all(c.confidence < align.REVIEW_BELOW for c in mine), [c.confidence for c in mine])


class TranscriptStillFindsFootage(unittest.TestCase):
    def test_ranking_keeps_the_speech(self):
        pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
        found = ev.gather(["v1c1", "v1c2"])
        found["v1c1"].add(SAID)
        scorer = align.Scorer(pool, found)
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        relevance = scorer.relevance(unit, pool[0])[0]
        self.assertGreater(relevance, align.MIN_RELEVANCE)  # the speech still ranks the shot
        self.assertEqual(scorer.content_relevance(unit, pool[0], relevance), 0)  # it never vouches for it
        self.assertEqual(scorer.unspoken_relevance(unit, pool[0], relevance), 0)

    def test_a_file_name_still_counts_for_coverage(self):
        # P2-14 is unchanged: a file name ranks a shot and keeps its coverage, it is never trusted for the confidence
        pool = [_candidate("v1c1", 0, 6000)]
        found = ev.gather(["v1c1"])
        found["v1c1"].add(ev.Item("metadata", 700, "qua dia cau xoay lanh tho rong lon", (), "tên file"))
        found["v1c1"].add(ev.Item("vision", 400, "Cảnh mờ.", (), "AI"))
        scorer = align.Scorer(pool, found)
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        relevance = scorer.relevance(unit, pool[0])[0]
        self.assertEqual(scorer.unspoken_relevance(unit, pool[0], relevance), relevance)

    def test_the_targeted_look_goes_to_the_shots_the_speech_is_about(self):
        # a sentence the speech alone matches is partly covered, so the targeted vision look is asked for, and the
        # shot the speech is about is looked at first (localisation, not verification)
        pool = [shots.Candidate(f"v1c{n}", "v1", n * 6000, n * 6000 + 6000, n, 50, shots.Quality(120, 50, 300, ()),
                                n * 2**20 + 1, f"k{n}.jpg") for n in range(1, 6)]
        found = ev.gather(c.candidate_id for c in pool)
        found["v1c4"].add(SAID)
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        _, (decision,), _ = align.assign([unit], pool, found, PACING, forward_only=True)
        self.assertIn(decision.coverage["status"], ("partial", "none"))
        picked = seeing.targets([decision.to_dict()], pool, found, set(), 2, forward_only=True)
        self.assertEqual(picked[0], "v1c4")


class FragmentsAndCaptions(unittest.TestCase):
    def test_t6_a_spoken_fragment_is_not_verified_by_a_seen_one(self):
        # one sentence, two pieces: the first shot was seen and shows the sentence, the second is known only from the
        # speech over it; the second must not ride on the first
        pool = [_candidate("v1c1", 0, 2000), _candidate("v1c2", 2000, 4500), _candidate("v1c3", 4500, 12_000)]
        found = ev.gather(["v1c1", "v1c2", "v1c3"])
        found["v1c1"].add(STRONG_VISION)
        found["v1c2"].add(SAID)
        state, mine, decision = _plan(pool, found)
        pieces = {c.candidate.candidate_id: c for c in mine}
        self.assertIn("v1c2", pieces, [c.candidate.candidate_id for c in mine])
        self.assertNotEqual(state, rv.READY)
        self.assertIn("v1c1", pieces, [c.candidate.candidate_id for c in mine])
        self.assertTrue(pieces["v1c2"].needs_review)
        self.assertLess(pieces["v1c2"].confidence, align.REVIEW_BELOW)  # 3ecaf7d: 680
        self.assertGreaterEqual(pieces["v1c1"].confidence, align.REVIEW_BELOW)  # the seen piece keeps its own
        self.assertEqual(decision.coverage["illustrated_ms"], pieces["v1c1"].length_ms)
        self.assertIn(f"{pieces['v1c2'].length_ms / 1000:.1f} giây cảnh chỉ khớp lời nói trong nguồn", " ".join(decision.notes))

    def _part(self, cues: tuple):
        found = scene.Analysis(30_000, (6000, 12_000, 18_000, 24_000),
                               tuple(scene.Shot(a, a + 6000, 50) for a in range(0, 30_000, 6000)), (), True, "x" * 64)
        source = compiler.Footage("i1-s1", Path("tau.mp4"), found, 640, 360, (25, 1), cues=cues)
        pool = [shots.Candidate(f"i1-s1c{n + 1}", "i1-s1", n * 6000, n * 6000 + 6000, n, 50, shots.Quality(120, 50, 300),
                                n * 2**20 + 1) for n in range(5)]
        evidence = compiler.evidence_for([source], {"i1-s1": pool}, {})
        # what the vision saw: the train at the station only in c1; c2 and c3 show a crowd and a port
        evidence["i1-s1c1"].add(ev.Item("vision", 900, "Đoàn tàu hơi nước dừng ở sân ga đông người.", (), "AI"))
        evidence["i1-s1c2"].add(ev.Item("vision", 850, "Đám đông đứng trên quảng trường.", (), "AI"))
        evidence["i1-s1c3"].add(ev.Item("vision", 850, "Bến cảng vắng, sóng nhẹ.", (), "AI"))
        spoken = [("Đoàn tàu hơi nước dừng ở sân ga.", 2500), ("Quân đội tiến vào thành phố Kursk.", 2500)]
        made = compiler.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                  footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Kursk",
                                  inputs={"part": "0" * 64})
        log = rv.annotate(made.log, made.plan.to_dict())
        return {u["unit_id"]: (u["review"]["state"], u["chosen"]) for u in log["units"]}

    def test_t5_source_captions_never_make_a_visual_ready(self):
        # the same part with and without the source's captions (captions_vi.srt beside the video): the captions say
        # "quân đội tiến vào thành phố Kursk" over the crowd and the port; they may move the choice, never make it READY
        without = self._part(())
        with_captions = self._part(((6100, 11_900, "quân đội tiến vào thành phố Kursk"),
                                    (12_100, 17_900, "quân đội tiến vào thành phố Kursk")))
        ready = lambda states: {u for u, (state, _) in states.items() if state == rv.READY}
        self.assertLessEqual(ready(with_captions), ready(without), (without, with_captions))
        second = [u for u in with_captions if u.endswith("u2")][0]
        self.assertNotEqual(with_captions[second][0], rv.READY, with_captions)
        first = [u for u in with_captions if u.endswith("u1")][0]
        self.assertEqual(with_captions[first][0], without[first][0])  # the seen train is untouched


class LegacyPaths(unittest.TestCase):
    def test_the_log_keeps_the_contract_keys(self):
        # a sentence planned on speech writes no key the editorial contract (additionalProperties false) does not have
        import json
        defs = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                          .read_text(encoding="utf-8"))["$defs"]["unit"]["properties"]
        for items in ((SAID,), (WEAK_VISION, SAID), (OTHER_VISION, SAID)):
            _, _, decision = _one_shot(*items)
            written = decision.to_dict()
            self.assertLessEqual(set(written), set(defs))
            self.assertLessEqual(set(written["coverage"]), set(defs["coverage"]["properties"]))
            self.assertLessEqual(set(written["signals"]), set(defs["signals"]["properties"]))

    def test_t7_evidence_without_speech_is_planned_as_before(self):
        # no transcript, no file name: what is trusted and what is relevant are what they were
        pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
        found = ev.gather(["v1c1", "v1c2"])
        found["v1c1"].add(STRONG_VISION)
        found["v1c2"].add(ev.Item("user", 1000, "đám đông", (), "ghi chú của bạn"))
        scorer = align.Scorer(pool, found)
        self.assertEqual(scorer.named, set())
        self.assertEqual(scorer.spoken, set())
        unit = align.Unit("u1", TEXT, 0, 4000, "narration")
        for c in pool:
            relevance = scorer.relevance(unit, c)[0]
            self.assertEqual(scorer.content_relevance(unit, c, relevance), relevance)
            self.assertEqual(scorer.unspoken_relevance(unit, c, relevance), relevance)
        self.assertTrue(found["v1c2"].seen)  # the user's own word still verifies (#836 leaves it as it was)

    def test_seen_and_content_differ_only_by_the_speech(self):
        said_only = ev.Evidence("a", [SAID])
        self.assertTrue(said_only.content)  # still content: the log mode and the review panel read it as before
        self.assertFalse(said_only.seen)
        self.assertTrue(ev.Evidence("b", [WEAK_VISION]).seen)
        self.assertFalse(ev.Evidence("c", [ev.Item("metadata", 700, "wwf 01", (), "tên file")]).seen)

    def test_the_coverage_map_counts_only_what_was_seen(self):
        pool = [_candidate("v1c1", 0, 6000), _candidate("v1c2", 6000, 12_000)]
        found = ev.gather(["v1c1", "v1c2"])
        found["v1c1"].add(SAID)
        found["v1c2"].add(WEAK_VISION)
        made = seeing.coverage_map(pool, found)
        self.assertEqual(made["shots_with_evidence"], 1)
        self.assertEqual(made["unseen"], [["v1", 0, 6000]])


if __name__ == "__main__":
    unittest.main()

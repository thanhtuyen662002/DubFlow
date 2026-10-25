from copy import deepcopy
import unittest

from engine.dubflow.worker.b2_audio import _dubbing_phrases, _dubbing_windows, _joined_text


def cue(identifier, start, end, text="chào", confidence=.9):
    return {"cue_id": identifier, "start_ms": start, "end_ms": end,
            "translated_text": text, "source_text": "你好", "confidence": confidence}


class DubbingPhraseTests(unittest.TestCase):
    def group(self, cues, **options):
        return _dubbing_phrases(cues, default_voice="primary", **options)

    def test_gap_is_strict_and_overlapping_cues_join_in_source_order(self):
        cues = [cue("a", 0, 1000), cue("b", 1249, 1500), cue("c", 1400, 1800),
                cue("d", 2050, 2400), cue("e", 2400, 2700)]
        before = deepcopy(cues)
        phrases = self.group(list(reversed(cues)))
        self.assertEqual([p["member_cue_ids"] for p in phrases], [["a", "b", "c"], ["d", "e"]])
        self.assertEqual([(p["cue_id"], p["start_ms"], p["end_ms"]) for p in phrases],
                         [("phrase-a", 0, 1800), ("phrase-d", 2050, 2700)])
        self.assertEqual(cues, before)
        self.assertEqual(phrases, self.group(cues))

    def test_span_count_and_text_bounds_are_inclusive(self):
        for end, expected in ((8000, 1), (8001, 2)):
            with self.subTest(end=end):
                self.assertEqual(len(self.group([cue("a", 0, 4000), cue("b", 4000, end)])), expected)
        phrases = self.group([cue(str(i), i * 100, (i + 1) * 100) for i in range(6)])
        self.assertEqual([len(p["member_cue_ids"]) for p in phrases], [5, 1])
        for length, expected in ((255, 1), (256, 2)):
            with self.subTest(length=length):
                phrases = self.group([cue("a", 0, 1000, "a" * 255), cue("b", 1000, 2000, "b" * length)])
                self.assertEqual(len(phrases), expected)
        self.assertEqual(len(self.group([cue("a", 0, 100, "x" * 513), cue("b", 100, 200)])), 2)

    def test_voice_and_speaker_changes_split_even_when_cast_to_the_same_voice(self):
        cues = [cue("a", 0, 500), cue("b", 500, 1000), cue("c", 1000, 1500), cue("d", 1500, 2000)]
        phrases = self.group(cues, voice_by_cue={"c": "other", "d": "other"},
                             speaker_by_cue={"a": "S1", "b": "S2", "c": "S2", "d": "S2"})
        self.assertEqual([p["member_cue_ids"] for p in phrases], [["a"], ["b"], ["c", "d"]])
        # Equal voices on either side of a different speaker are not consecutive.
        self.assertEqual([p["cue_id"] for p in self.group(cues[:3], speaker_by_cue={"b": "S2"})], ["a", "b", "c"])

    def test_joined_text_uses_previous_sentence_punctuation(self):
        for ending in (".", "!", "?", "…", "...", "?!"):
            self.assertEqual(_joined_text("Chào" + ending, "tiếp"), "Chào" + ending + " tiếp")
        self.assertEqual(_joined_text("chào", "tiếp"), "chào, tiếp")
        self.assertEqual(self.group([cue("a", 0, 500, "Chào."), cue("b", 500, 1000, "tiếp"),
                                    cue("c", 1000, 1500, "nữa?")])[0]["translated_text"], "Chào. tiếp, nữa?")

    def test_singleton_keeps_all_input_fields_and_window_identity(self):
        original = cue("unchanged", 1500, 2000, "  Xin chào!  ", .6)
        phrase, = self.group([original])
        self.assertEqual({key: phrase[key] for key in original}, original)
        self.assertEqual(phrase["member_cue_ids"], [original["cue_id"]])
        self.assertEqual(_dubbing_windows([phrase], 6000), _dubbing_windows([original], 6000))

    def test_phrase_window_uses_last_member_and_next_phrase_with_guard(self):
        phrases = self.group([cue("a", 0, 500), cue("b", 500, 1000), cue("c", 5000, 5500)])
        for postroll, expected in ((2000, 3000), (4000, 4880)):
            windows = _dubbing_windows(phrases, 9000, max_postroll_ms=postroll)
            self.assertEqual(int(windows[0]["slot_end"]["ticks"]), 1000)
            self.assertEqual(int(windows[0]["render_window_end"]["ticks"]), expected)
            self.assertFalse(windows[0]["overlaps_source"])
        # An overlap across a speaker boundary still forbids postroll.
        phrases = self.group([cue("a", 0, 1000), cue("b", 900, 2000)], speaker_by_cue={"b": "S2"})
        self.assertEqual([int(w["render_window_end"]["ticks"]) for w in _dubbing_windows(phrases, 9000)], [1000, 2000])

    def test_nested_members_do_not_hide_overlap_with_the_next_speaker(self):
        phrases = self.group([cue("outer", 0, 5000), cue("inner", 1000, 2000), cue("next", 2500, 3000)],
                             speaker_by_cue={"next": "S2"})
        self.assertEqual(phrases[0]["end_ms"], 2000)  # The last member's end is the phrase slot end.
        self.assertTrue(all(w["overlaps_source"] for w in _dubbing_windows(phrases, 9000)))

    def test_generated_ids_cannot_collide_with_singletons_or_exceed_the_tts_bound(self):
        cues = [cue("a", 0, 100), cue("b", 100, 200), cue("phrase-a", 1000, 1200),
                cue("x" * 256, 2000, 2100), cue("last", 2100, 2200)]
        phrases = self.group(cues)
        identifiers = [p["cue_id"] for p in phrases]
        self.assertEqual(len(set(identifiers)), 3)
        self.assertEqual(identifiers[1], "phrase-a")
        self.assertTrue(all(len(identifier) <= 256 for identifier in identifiers))


if __name__ == "__main__":
    unittest.main()

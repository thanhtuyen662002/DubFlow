from dataclasses import dataclass
import unittest

from engine.dubflow.translation.sensitive import SensitivePolicy, SensitiveWordsError, apply_to_cues, default_policy_mapping


@dataclass(frozen=True)
class Cue:
    cue_id: str
    translated_text: str | None


class SensitiveWordsTest(unittest.TestCase):
    def test_variant_mode_respells_whole_words_in_subtitles_and_voice(self):
        policy = SensitivePolicy.from_mapping(default_policy_mapping("variant"))
        line = "Chết rồi! Hắn GIẾT người bằng dao, xác chết còn đó."
        self.assertEqual(policy.subtitle(line), "Chớt rồi! Hắn GIỚT người bằng dzao, xác chớt còn đó.")
        self.assertEqual(policy.spoken(line), policy.subtitle(line))

    def test_beep_mode_masks_the_subtitle_and_keeps_a_harmless_word_under_the_beep(self):
        policy = SensitivePolicy.from_mapping(default_policy_mapping("beep"))
        self.assertEqual(policy.subtitle("Anh ta bị giết bằng dao"), "Anh ta bị gi*t bằng d*o")
        self.assertEqual(policy.spoken("Anh ta bị giết bằng dao"), "Anh ta bị giớt bằng dzao")
        self.assertEqual([word for _, _, word in policy.matches("Anh ta bị giết bằng dao")], ["giết", "dao"])

    def test_everyday_words_and_parts_of_words_are_left_alone(self):
        policy = SensitivePolicy.from_mapping(default_policy_mapping("variant"))
        self.assertEqual(policy.subtitle("dao động mạnh, máy chết máy, chếtđi"), "dao động mạnh, máy chết máy, chếtđi")

    def test_decomposed_unicode_matches(self):
        policy = SensitivePolicy.from_mapping({"mode": "variant", "words": [{"from": "chết", "to": "chớt"}]})
        self.assertEqual(policy.subtitle("chết"), "chớt")

    def test_cues_change_only_where_needed(self):
        policy = SensitivePolicy.from_mapping({"mode": "variant", "words": [{"from": "Giết", "to": "giớt"}]})
        result, changed = apply_to_cues([Cue("a", "giết nó"), Cue("b", "chào"), Cue("c", None)], policy, spoken=False)
        self.assertEqual([cue.translated_text for cue in result], ["giớt nó", "chào", None])
        self.assertEqual(changed, 1)
        self.assertEqual(apply_to_cues([Cue("a", "giết")], None, spoken=True)[1], 0)

    def test_bad_settings_are_refused(self):
        for value in ({"mode": "x", "words": []}, {"mode": "beep", "words": [{"from": "", "to": "a"}]},
                      {"mode": "beep", "words": [{"from": "a" * 41, "to": "a"}]}, {"mode": "beep"}):
            with self.subTest(value=value), self.assertRaises(SensitiveWordsError):
                SensitivePolicy.from_mapping(value)


if __name__ == "__main__":
    unittest.main()

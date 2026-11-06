from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import unittest

from engine.dubflow.ai.client import AiError, make_route
from engine.dubflow.translation import context_ai


@dataclass(frozen=True)
class Cue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None = None


def cues(count):
    return [Cue(f"c{index}", index * 2000, index * 2000 + 1800, f"你好{index}", f"Xin chào {index}") for index in range(count)]


ROUTE = make_route("deepseek", "sk-test-0123456789")
BRIEF = {"tom_tat": "Hai mẹ con nói chuyện.", "the_loai": "phim ngắn", "nhan_vat": [{"ten": "Mẹ", "vai": "mẹ", "xung_ho": "mẹ - con"}],
         "thuat_ngu": [{"goc": "李明", "viet": "Lý Minh"}]}


class Asker:
    """Brief first, then each window: answer with natural lines (or a queued failure)."""

    def __init__(self, failures=()):
        self.failures = list(failures)
        self.prompts = []

    def __call__(self, route, system, schema, parts, **_):
        self.prompts.append(parts[0]["text"])
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        if schema is context_ai.BRIEF_SCHEMA:
            return BRIEF, route
        ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
        return {"lines": [{"id": cue_id, "text": f"Con chào mẹ ạ {cue_id}"} for cue_id in ids], "ghi_chu": f"đã tới {ids[-1]}"}, route


class ContextTranslationTest(unittest.TestCase):
    def test_windows_carry_the_brief_the_previous_lines_and_the_notes(self):
        asker = Asker()
        result = context_ai.translate_in_context(cues(90), ROUTE, dubbing=True, asker=asker, title="Mẹ ơi")
        self.assertTrue(result.complete)
        self.assertEqual((result.windows_total, result.windows_done, result.cues_from_ai), (3, 3, 90))
        self.assertEqual(result.cues[0].translated_text, "Con chào mẹ ạ c0")
        self.assertEqual([cue.cue_id for cue in result.cues], [f"c{index}" for index in range(90)])
        second = asker.prompts[2]
        self.assertIn("xưng hô: mẹ - con", second)
        self.assertIn("đã tới c39", second)
        self.assertIn("Con chào mẹ ạ c39", second)  # the line just before, already in Vietnamese
        self.assertIn('"toi_da_tieng": 8', second)  # 1.8 s at 4.5 syllables per second
        self.assertIn("Tên video: Mẹ ơi", second)

    def test_a_failed_window_keeps_its_offline_lines_and_is_reported(self):
        asker = Asker([None, None, AiError("SERVER", "busy")])
        result = context_ai.translate_in_context(cues(90), ROUTE, dubbing=False, asker=asker)
        self.assertFalse(result.complete)
        self.assertEqual(result.windows_done, 2)
        self.assertEqual(result.cues[45].translated_text, "Xin chào 45")
        self.assertEqual(result.error, "busy")

    def test_a_rejected_key_stops_at_once_with_every_offline_line(self):
        asker = Asker([AiError("KEY", "Khóa DeepSeek không hợp lệ")])
        result = context_ai.translate_in_context(cues(10), ROUTE, dubbing=True, asker=asker)
        self.assertEqual((result.windows_done, len(asker.prompts)), (0, 1))
        self.assertEqual([cue.translated_text for cue in result.cues], [cue.translated_text for cue in cues(10)])
        self.assertIn("không hợp lệ", result.error)

    def test_unknown_ids_overlong_and_empty_lines_keep_the_draft(self):
        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return BRIEF, route
            return {"lines": [{"id": "c0", "text": "Mẹ ơi " * 30}, {"id": "c1", "text": "  ✨ "}, {"id": "zz", "text": "lạ"},
                              {"id": "c2", "text": "**Được rồi** con đi đây 😀"}], "ghi_chu": ""}, route
        result = context_ai.translate_in_context(cues(3), ROUTE, dubbing=True, asker=asker)
        self.assertEqual([cue.translated_text for cue in result.cues], ["Xin chào 0", "Xin chào 1", "Được rồi con đi đây"])

    def test_skipped_lines_are_asked_once_more(self):
        asked = []

        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return BRIEF, route
            ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
            asked.append(ids)
            return {"lines": [{"id": cue_id, "text": f"Câu {cue_id} đã dịch"} for cue_id in ids if cue_id != "c1" or len(asked) > 1],
                    "ghi_chu": "ghi chú"}, route
        result = context_ai.translate_in_context(cues(3), ROUTE, dubbing=True, asker=asker)
        self.assertEqual(asked, [["c0", "c1", "c2"], ["c1"]])
        self.assertEqual([cue.translated_text for cue in result.cues], ["Câu c0 đã dịch", "Câu c1 đã dịch", "Câu c2 đã dịch"])
        self.assertEqual(result.cues_from_ai, 3)

    def test_unaccented_answers_are_asked_again(self):
        calls = []

        def asker(route, system, schema, parts, **_):
            calls.append(schema)
            if schema is context_ai.BRIEF_SCHEMA:
                return BRIEF, route
            text = "Con chao me a, hom nay con di hoc ve muon vi tac duong qua" if len(calls) == 2 else "Con chào mẹ ạ, hôm nay con đi học về muộn vì tắc đường quá"
            return {"lines": [{"id": "c0", "text": text}], "ghi_chu": ""}, route
        result = context_ai.translate_in_context(cues(1), ROUTE, dubbing=False, asker=asker)
        self.assertEqual(result.cues[0].translated_text, "Con chào mẹ ạ, hôm nay con đi học về muộn vì tắc đường quá")
        self.assertEqual(len(calls), 3)

    def test_finished_windows_are_cached_by_their_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Asker()
            context_ai.translate_in_context(cues(50), ROUTE, dubbing=True, asker=first, cache_dir=Path(folder))
            again = Asker()
            result = context_ai.translate_in_context(cues(50), ROUTE, dubbing=True, asker=again, cache_dir=Path(folder))
            self.assertEqual(again.prompts, [])
            self.assertTrue(result.complete)
            other = Asker()
            context_ai.translate_in_context(cues(50), make_route("deepseek", "sk-test-0123456789", model="other"), dubbing=True,
                                            asker=other, cache_dir=Path(folder))
            self.assertEqual(len(other.prompts), 3)

    def test_identity_never_holds_the_key(self):
        self.assertNotIn("sk-test", json.dumps(context_ai.identity(ROUTE, dubbing=True)))


if __name__ == "__main__":
    unittest.main()

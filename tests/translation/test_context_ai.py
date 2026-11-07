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
        # Brief, window 1, window 2 fails, window 3, window 2 again fails: the offline lines stay.
        asker = Asker([None, None, AiError("SERVER", "busy"), None, AiError("SERVER", "busy")])
        result = context_ai.translate_in_context(cues(90), ROUTE, dubbing=False, asker=asker)
        self.assertEqual(len(asker.prompts), 5)
        self.assertFalse(result.complete)
        self.assertEqual(result.windows_done, 2)
        self.assertEqual(result.cues[45].translated_text, "Xin chào 45")
        self.assertEqual(result.error, "busy")

    def test_a_window_cut_by_the_network_is_tried_again_after_the_others(self):
        asker = Asker([None, None, AiError("NETWORK", "Kết nối bị ngắt giữa chừng")])
        result = context_ai.translate_in_context(cues(130), ROUTE, dubbing=False, asker=asker)
        self.assertTrue(result.complete)
        self.assertEqual((result.windows_done, result.cues_from_ai), (4, 130))
        self.assertIn("Đây là đoạn 2/4", asker.prompts[-1])  # retried last, after windows 3 and 4
        self.assertIn("đã tới c39", asker.prompts[-1])  # with window 1's notes, as the first time

    def test_a_rejected_key_stops_at_once_with_every_offline_line(self):
        asker = Asker([AiError("KEY", "Khóa DeepSeek không hợp lệ")])
        result = context_ai.translate_in_context(cues(10), ROUTE, dubbing=True, asker=asker)
        self.assertEqual((result.windows_done, len(asker.prompts)), (0, 1))
        self.assertEqual([cue.translated_text for cue in result.cues], [cue.translated_text for cue in cues(10)])
        self.assertIn("không hợp lệ", result.error)

    def test_a_rate_limited_window_is_retried_after_a_pause_and_a_used_up_day_stops(self):
        pauses = []
        asker = Asker([None, None, AiError("LIMIT", "Đã vượt giới hạn số lần gọi Gemini")])
        result = context_ai.translate_in_context(cues(90), ROUTE, dubbing=False, asker=asker, pause=pauses.append)
        self.assertTrue(result.complete)
        self.assertEqual(pauses, [context_ai.RATE_LIMIT_PAUSE_SECONDS])
        # A day's free quota used up: no window after it is asked, the offline lines stay, no pause.
        pauses.clear()
        asker = Asker([None, None, AiError("QUOTA", "Khóa Gemini đã hết lượt gọi của hôm nay")])
        result = context_ai.translate_in_context(cues(90), ROUTE, dubbing=False, asker=asker, pause=pauses.append)
        self.assertEqual((result.windows_done, len(asker.prompts), pauses), (1, 3, []))
        self.assertIn("hôm nay", result.error)
        self.assertEqual(result.cues[60].translated_text, "Xin chào 60")

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

    def test_a_person_keeps_one_third_person_word_across_the_video(self):
        brief = {**BRIEF, "nhan_vat": [{"ten": "Người đàn ông", "vai": "nhân vật chính", "xung_ho": "người kể gọi anh ta", "goi_ngoi_ba": "anh ta"}]}
        drafts = {"c0": "Anh ta lấy khăn giấy", "c1": "Gã lại hì hục cả buổi", "c2": "Ông lão hắng giọng"}
        asked = []

        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return brief, route
            text = parts[0]["text"]
            ids = [json.loads(line)["id"] for line in text.splitlines() if line.startswith('{"id"')]
            if system is context_ai.UNIFY_SYSTEM:
                asked.append(ids)
                self.assertIn("anh ta", text)
                return {"lines": [{"id": "c1", "text": "Anh ta lại hì hục cả buổi"}], "ghi_chu": ""}, route
            return {"lines": [{"id": cue_id, "text": drafts[cue_id]} for cue_id in ids], "ghi_chu": ""}, route

        with tempfile.TemporaryDirectory() as folder:
            result = context_ai.translate_in_context(cues(3), ROUTE, dubbing=False, asker=asker, cache_dir=Path(folder))
            self.assertEqual(asked, [["c1"]])  # only the line that drifted is sent, "ông lão hắng" is not a match
            self.assertEqual([cue.translated_text for cue in result.cues],
                             ["Anh ta lấy khăn giấy", "Anh ta lại hì hục cả buổi", "Ông lão hắng giọng"])
            again = context_ai.translate_in_context(cues(3), ROUTE, dubbing=False, asker=asker, cache_dir=Path(folder))
            self.assertEqual(len(asked), 1)  # the rewrite is cached with the windows
            self.assertEqual(again.cues[1].translated_text, "Anh ta lại hì hục cả buổi")

    def test_a_failed_unify_pass_keeps_the_window_lines(self):
        brief = {**BRIEF, "nhan_vat": [{"ten": "A", "vai": "chính", "xung_ho": "", "goi_ngoi_ba": "anh ta"}]}

        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return brief, route
            if system is context_ai.UNIFY_SYSTEM:
                raise AiError("SERVER", "busy")
            ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
            return {"lines": [{"id": cue_id, "text": "Hắn bỏ đi rồi"} for cue_id in ids], "ghi_chu": ""}, route

        result = context_ai.translate_in_context(cues(2), ROUTE, dubbing=False, asker=asker)
        self.assertTrue(result.complete)
        self.assertEqual({cue.translated_text for cue in result.cues}, {"Hắn bỏ đi rồi"})
        self.assertEqual((result.unify_pending, result.unify_error), (2, "busy"))  # reported, not silent

    def test_with_two_people_a_chosen_word_for_the_wrong_person_is_still_checked(self):
        brief = {**BRIEF, "nhan_vat": [{"ten": "Chồng", "vai": "", "xung_ho": "", "goi_ngoi_ba": "anh ta"},
                                       {"ten": "Vợ", "vai": "", "xung_ho": "", "goi_ngoi_ba": "cô ấy"}]}
        drafts = {"c0": "Anh ta mở cửa", "c1": "Cô ấy cạo râu rồi đi làm", "c2": "Trời mưa to"}
        asked = []

        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return brief, route
            ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
            if system is context_ai.UNIFY_SYSTEM:
                asked.append(ids)
                return {"lines": [{"id": "c0", "text": "Anh ta mở cửa"}, {"id": "c1", "text": "Anh ta cạo râu rồi đi làm"}], "ghi_chu": ""}, route
            return {"lines": [{"id": cue_id, "text": drafts[cue_id]} for cue_id in ids], "ghi_chu": ""}, route

        result = context_ai.translate_in_context(cues(3), ROUTE, dubbing=False, asker=asker)
        self.assertEqual(asked, [["c0", "c1"]])  # every line naming someone, not the line without
        self.assertEqual(result.cues[1].translated_text, "Anh ta cạo râu rồi đi làm")
        self.assertEqual(result.unify_pending, 0)

    def test_every_drifted_line_is_checked_in_batches_and_a_failed_batch_is_counted(self):
        brief = {**BRIEF, "nhan_vat": [{"ten": "A", "vai": "", "xung_ho": "", "goi_ngoi_ba": "anh ta"}]}
        batches = []

        def asker(route, system, schema, parts, **_):
            if schema is context_ai.BRIEF_SCHEMA:
                return brief, route
            ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
            if system is context_ai.UNIFY_SYSTEM:
                batches.append(len(ids))
                if len(batches) == 2:
                    raise AiError("TIMEOUT", "chậm quá")
                return {"lines": [{"id": cue_id, "text": "Anh ta đi tiếp"} for cue_id in ids], "ghi_chu": ""}, route
            return {"lines": [{"id": cue_id, "text": "Gã đi tiếp"} for cue_id in ids], "ghi_chu": ""}, route

        total = 2 * context_ai.UNIFY_BATCH + 5
        result = context_ai.translate_in_context(cues(total), ROUTE, dubbing=False, asker=asker)
        self.assertEqual(batches, [context_ai.UNIFY_BATCH, context_ai.UNIFY_BATCH, 5])
        self.assertEqual(result.unify_pending, context_ai.UNIFY_BATCH)
        self.assertEqual(sum(cue.translated_text == "Anh ta đi tiếp" for cue in result.cues), context_ai.UNIFY_BATCH + 5)

    def test_stray_references_match_whole_words_only(self):
        self.assertEqual(context_ai.stray_references("Gã hắng giọng, anh ta cười", {"anh ta"}), ["gã"])
        self.assertEqual(context_ai.stray_references("Cô ấy đi ả đào", {"cô ấy", "ả"}), [])
        self.assertEqual(context_ai.stray_references("thằng nhỏ chạy", {"anh ta"}), [])

    def test_a_bare_anh_next_to_anh_ta_is_a_drift(self):
        # 1.1.76 on the owner PC: "Anh ta sắp phát điên…" then "Lúc này anh mới nhận ra…".
        self.assertEqual(context_ai.stray_references("Lúc này anh mới nhận ra", {"anh ta"}), ["anh"])
        self.assertEqual(context_ai.stray_references("Anh ta sắp phát điên", {"anh ta"}), [])
        self.assertEqual(context_ai.stray_references("Ánh đèn tắt, anh ấy về", {"anh ấy"}), [])
        self.assertEqual(context_ai.stray_references("anh trai của hắn", {"hắn"}), [])

    def test_anh_as_kinship_or_title_is_not_a_drift(self):
        for text in ("Anh trai của hắn", "Anh em của hắn", "Người anh của hắn về", "anh Minh gật đầu",
                     "Hắn gọi anh của mình", "hai anh em cãi nhau"):
            with self.subTest(text=text):
                self.assertNotIn("anh", context_ai.stray_references(text, {"anh ta"}))
        self.assertEqual(context_ai.stray_references("Anh trai về, lúc này anh mới nhận ra", {"anh ta"}), ["anh"])
        self.assertEqual(context_ai.stray_references("Rồi anh đã bỏ đi", {"anh ta"}), ["anh"])

    def test_identity_never_holds_the_key(self):
        self.assertNotIn("sk-test", json.dumps(context_ai.identity(ROUTE, dubbing=True)))


if __name__ == "__main__":
    unittest.main()

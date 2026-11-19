"""Thuật ngữ và ngữ cảnh (Issue #903): resolving, checking lines, and the AI pass. No service is called."""
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from engine.dubflow.ai.client import make_route
from engine.dubflow.translation import context_ai, glossary
from engine.dubflow.worker import production_job


@dataclass(frozen=True)
class Cue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None = None


def term(source, target, mandatory=False, **extra):
    return {"source": source, "target": target, "mandatory": mandatory, **extra}


class ResolveTest(unittest.TestCase):
    def test_priority_mandatory_then_project_then_shared(self):
        guide = glossary.resolve({
            "project": [term("李明", "Lý Minh"), term("霸道总裁", "tổng tài bá đạo", True)],
            "shared": [
                {"name": "Phim", "terms": [term("李明", "Lí Minh", True), term("王爷", "Vương gia"), term("霸道总裁", "chủ tịch")]},
                {"name": "Khác", "terms": [term("王爷", "vương gia"), term("DubFlow", "DubFlow", True)]},
            ],
        })
        chosen = {item.source: (item.target, item.origin, item.mandatory) for item in guide.terms}
        # A shared mandatory term beats a preferred project term; a project mandatory one beats everything.
        self.assertEqual(chosen["李明"], ("Lí Minh", "shared:Phim", True))
        self.assertEqual(chosen["霸道总裁"], ("tổng tài bá đạo", "project", True))
        self.assertEqual(chosen["王爷"], ("Vương gia", "shared:Phim", False), "the first listed shared glossary wins at one level")
        self.assertEqual(guide.duplicates, 1, "Vương gia / vương gia is one spelling")
        self.assertEqual(sorted((c.source, c.kept.target, c.dropped.target) for c in guide.conflicts),
                         [("李明", "Lí Minh", "Lý Minh"), ("霸道总裁", "tổng tài bá đạo", "chủ tịch")])
        self.assertEqual([item.level for item in guide.terms][:2], ["mandatory_project", "mandatory_shared"])

    def test_text_is_cleaned_and_half_rows_and_bad_shapes_are_handled(self):
        guide = glossary.resolve({"project": [term("  Tiktok​  shop ", " TikTok\nShop "), term("", "x"), term("y", "")],
                                  "context": {"topic": "Review\x00 nồi chiên", "people": "Chủ shop xưng em,\ngọi khách là chị", "tone": 3}})
        self.assertEqual([(item.source, item.target) for item in guide.terms], [("Tiktok shop", "TikTok Shop")])
        self.assertEqual(guide.context.topic, "Review nồi chiên")
        self.assertEqual(guide.context.people, "Chủ shop xưng em, gọi khách là chị")
        self.assertEqual(guide.context.tone, "")
        for bad in (["x"], {"project": "x"}, {"project": ["x"]}, {"shared": {"a": 1}}, {"context": "x"}):
            with self.assertRaises(glossary.GlossaryError):
                glossary.resolve(bad)
        self.assertTrue(glossary.resolve(None).empty())

    def test_identity_follows_what_the_translation_reads(self):
        one = glossary.resolve({"project": [term("a", "b", note="x")]})
        self.assertEqual(one.identity(), glossary.resolve({"project": [term("a", "b", note="khác")]}).identity())
        self.assertNotEqual(one.identity(), glossary.resolve({"project": [term("a", "c")]}).identity())
        self.assertNotEqual(one.identity(), glossary.resolve({"project": [term("a", "b", True)]}).identity())
        self.assertNotEqual(one.identity(), glossary.resolve({"project": [term("a", "b")], "context": {"tone": "hài"}}).identity())


class EnforceTest(unittest.TestCase):
    def test_the_offline_rendering_is_replaced_and_misses_are_reported(self):
        guide = glossary.resolve({"project": [term("李明", "Lý Minh", True), term("王爷", "Vương gia"), term("GPU", "card đồ họa", True)]})
        offline = {"李明": "Li Ming", "王爷": "hoàng tử", "GPU": "GPU"}
        cues = [
            Cue("1", 0, 1000, "李明来了", "Li Ming đã đến."),
            Cue("2", 0, 1000, "王爷请坐", "Hoàng tử mời ngồi."),
            Cue("3", 0, 1000, "李明，你好", "Chào anh Lý Minh."),
            Cue("4", 0, 1000, "李明走了", "Anh ấy đi rồi."),
            Cue("5", 0, 1000, "这块GPU很快", "GPU này rất nhanh."),
            Cue("6", 0, 1000, "你好", "Xin chào."),
        ]
        asked = []

        def render(text):
            asked.append(text)
            return offline[text]

        result = glossary.enforce(cues, guide, render)
        texts = [cue.translated_text for cue in result.cues]
        self.assertEqual(texts[0], "Lý Minh đã đến.")
        self.assertEqual(texts[1], "Vương gia mời ngồi.", "a preferred term is placed too, keeping the capital")
        self.assertEqual(texts[2], "Chào anh Lý Minh.", "already right: untouched")
        self.assertEqual(texts[3], "Anh ấy đi rồi.", "never guessed")
        self.assertEqual(texts[4], "Card đồ họa này rất nhanh.", "a term the translator left as it was, capital kept")
        self.assertIs(result.cues[5], cues[5])
        self.assertEqual(result.replaced, 3)
        self.assertEqual(result.unconfirmed, (("4", "李明"),))
        self.assertEqual(sorted(asked), ["GPU", "李明", "王爷"], "each term rendered once")

    def test_words_match_whole_and_unicode_case_insensitively(self):
        guide = glossary.resolve({"project": [term("pin", "pin sạc", True), term("Đà Lạt", "Đà Lạt city")]})
        cues = [Cue("1", 0, 1, "đổi pin", "đổi pin nhé"), Cue("2", 0, 1, "spinning", "đang quay"), Cue("3", 0, 1, "ĐÀ LẠT", "đà lạt đẹp")]
        result = glossary.enforce(cues, guide, lambda text: text)
        self.assertEqual([cue.translated_text for cue in result.cues], ["đổi pin sạc nhé", "đang quay", "Đà Lạt city đẹp"])
        self.assertEqual(result.unconfirmed, ())

    def test_a_failing_render_never_stops_the_others(self):
        guide = glossary.resolve({"project": [term("甲", "Giáp", True), term("乙", "Ất", True)]})

        def render(text):
            if text == "甲":
                raise RuntimeError("model")
            return "B"

        result = glossary.enforce([Cue("1", 0, 1, "甲乙", "A và B")], guide, render)
        self.assertEqual(result.cues[0].translated_text, "A và Ất")
        self.assertEqual(result.unconfirmed, (("1", "甲"),))


ROUTE = make_route("deepseek", "sk-test-0123456789")
BRIEF = {"tom_tat": "Phim cổ trang.", "the_loai": "phim", "nhan_vat": [], "thuat_ngu": [{"goc": "李明", "viet": "Lí Minh"}, {"goc": "长安", "viet": "Trường An"}]}


class Asker:
    def __init__(self):
        self.prompts = []

    def __call__(self, route, system, schema, parts, **_):
        self.prompts.append(parts[0]["text"])
        if schema is context_ai.BRIEF_SCHEMA:
            return BRIEF, route
        ids = [json.loads(line)["id"] for line in parts[0]["text"].splitlines() if line.startswith('{"id"')]
        return {"lines": [{"id": cue_id, "text": f"Câu dịch {cue_id}"} for cue_id in ids], "ghi_chu": ""}, route


class AiPassTest(unittest.TestCase):
    def test_the_user_context_and_terms_lead_and_replace_the_automatic_ones(self):
        guide = glossary.resolve({"project": [term("李明", "Lý Minh", True), term("王爷", "Vương gia"), term("不出现", "không có")],
                                  "context": {"topic": "Phim cổ trang cung đấu", "people": "Lý Minh xưng ta, gọi vua là phụ hoàng"}})
        asker = Asker()
        cues = [Cue(f"c{index}", index * 2000, index * 2000 + 1800, "李明见王爷" if index == 0 else "长安", "x") for index in range(3)]
        result = context_ai.translate_in_context(cues, ROUTE, dubbing=False, asker=asker, guide=guide)
        self.assertTrue(result.complete)
        brief_prompt, window_prompt = asker.prompts[0], asker.prompts[1]
        for prompt in (brief_prompt, window_prompt):
            self.assertIn("Chủ đề: Phim cổ trang cung đấu", prompt)
            self.assertIn("Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: 李明 → Lý Minh", prompt)
            self.assertIn("Thuật ngữ nên dùng (trừ khi sai ngữ cảnh): 王爷 → Vương gia", prompt)
            self.assertNotIn("不出现", prompt, "a term the video never says is not sent")
        self.assertIn("李明 → Lý Minh", window_prompt.split("Thuật ngữ: ")[1])
        self.assertNotIn("Lí Minh", window_prompt, "the brief's own spelling is replaced by the user's")
        self.assertIn("长安 → Trường An", window_prompt, "the brief's other terms stay")
        self.assertLess(window_prompt.index("Chủ đề:"), window_prompt.index("Tóm tắt:"), "the user's context comes before the automatic brief")

    def test_the_guide_is_part_of_the_cache_identity(self):
        guide = glossary.resolve({"project": [term("李明", "Lý Minh", True)]})
        self.assertNotIn("guide", context_ai.identity(ROUTE, dubbing=False))
        self.assertEqual(context_ai.identity(ROUTE, dubbing=False, guide=glossary.Guide()), context_ai.identity(ROUTE, dubbing=False))
        self.assertEqual(context_ai.identity(ROUTE, dubbing=False, guide=guide)["guide"], guide.identity())
        cues = [Cue("c0", 0, 1800, "李明", "x")]
        with TemporaryDirectory() as folder:
            first = Asker()
            context_ai.translate_in_context(cues, ROUTE, dubbing=False, asker=first, cache_dir=Path(folder))
            again = Asker()
            context_ai.translate_in_context(cues, ROUTE, dubbing=False, asker=again, cache_dir=Path(folder), guide=guide)
            self.assertEqual(len(again.prompts), 2, "a new glossary asks again instead of reusing the old answer")

    def test_no_guide_leaves_the_prompts_as_before(self):
        asker = Asker()
        context_ai.translate_in_context([Cue("c0", 0, 1800, "李明", "x")], ROUTE, dubbing=False, asker=asker)
        self.assertNotIn("BẮT BUỘC", "\n".join(asker.prompts))
        self.assertIn("李明 → Lí Minh", asker.prompts[1])


class SettingsTest(unittest.TestCase):
    def load(self, document):
        with TemporaryDirectory() as temp:
            (Path(temp) / production_job.TRANSLATION_SETTINGS_FILE).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            warnings: list[str] = []
            return production_job._translation_settings(SimpleNamespace(output_dir=Path(temp)), warnings), warnings

    def setUp(self):
        production_job._AI_KEY = None

    def test_the_glossary_is_read_and_a_bad_one_only_warns(self):
        settings, warnings = self.load({"schema_version": 1, "glossary": {"project": [term("李明", "Lý Minh", True)], "context": {"tone": "hài"}}})
        self.assertEqual([(item.source, item.target) for item in settings.guide.terms], [("李明", "Lý Minh")])
        self.assertEqual(settings.guide.context.tone, "hài")
        self.assertEqual(warnings, [])
        settings, warnings = self.load({"schema_version": 1, "glossary": ["x"]})
        self.assertTrue(settings.guide.empty())
        self.assertTrue(warnings[0].startswith("GLOSSARY_INVALID"))
        settings, warnings = self.load({"schema_version": 1})
        self.assertTrue(settings.guide.empty())

    def test_the_check_reports_in_provenance_and_warnings(self):
        guide = glossary.resolve({"project": [term("李明", "Lý Minh", True)]})
        runtime = SimpleNamespace(translate_text=lambda text: "Li Ming")
        cues = (production_job.TextCue("1", 0, 1000, "李明来了", "Li Ming đến.", 0.9), production_job.TextCue("2", 0, 1000, "李明", "Anh ấy.", 0.9))
        warnings: list[str] = []
        fixed, provenance = production_job._apply_glossary(cues, guide, runtime, {"backend": "argos"}, warnings)
        self.assertEqual(fixed[0].translated_text, "Lý Minh đến.")
        self.assertEqual(provenance["glossary_check"], {"terms": 1, "replaced": 1, "unconfirmed": 1})
        self.assertEqual(provenance["backend"], "argos")
        self.assertTrue(warnings[0].startswith("GLOSSARY_UNCONFIRMED: 1 câu"))

    def test_a_line_the_user_edited_is_never_rewritten_by_the_glossary(self):
        # Issue #953: the user's own edit of a line (translation_overrides.json) is applied after the glossary
        # check and wins over it, even when it does not use the mandatory term.
        guide = glossary.resolve({"project": [term("李明", "Lý Minh", True)], "context": {"topic": "Phim"}})
        runtime = SimpleNamespace(translate_text=lambda text: "Li Ming")
        cues = (production_job.TextCue("1", 0, 1000, "李明来了", "Li Ming đến.", 0.9), production_job.TextCue("2", 0, 1000, "李明走了", "Li Ming đi.", 0.9))
        fixed, _ = production_job._apply_glossary(cues, guide, runtime, {}, [])
        self.assertEqual([cue.translated_text for cue in fixed], ["Lý Minh đến.", "Lý Minh đi."])
        with TemporaryDirectory() as temp:
            out = Path(temp)
            (out / production_job.TRANSLATION_OVERRIDES_FILE).write_text(
                json.dumps({"schema_version": 1, "cues": {"2": "Li Ming rời đi."}}, ensure_ascii=False), encoding="utf-8")
            final, identity = production_job._apply_translation_overrides(fixed, out)
        self.assertEqual([cue.translated_text for cue in final], ["Lý Minh đến.", "Li Ming rời đi."])
        self.assertIsNotNone(identity)
        # Context is never turned into a term.
        self.assertEqual([item.source for item in guide.terms], ["李明"])


if __name__ == "__main__":
    unittest.main()

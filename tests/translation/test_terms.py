from __future__ import annotations

import io
import json
from pathlib import Path
import unittest

from engine.dubflow.translation import terms
from engine.dubflow.translation.adapter import TranslationError


class FakeRuntime:
    created: list[tuple] = []

    def __init__(self, app_root, model_root, language):
        FakeRuntime.created.append((Path(app_root), Path(model_root), language))
        self.prepared = False

    def prepare(self, model_root):
        self.prepared = True

    def translate_text(self, text):
        if not self.prepared:
            raise AssertionError("translate before prepare")
        if text == "坏":
            raise TranslationError("TRANSLATION_EMPTY", "nothing")
        return "vi:" + text


class BrokenRuntime(FakeRuntime):
    def prepare(self, model_root):
        raise TranslationError("TRANSLATION_PACKAGE_MISSING", "models not downloaded")


def run(argv, factory):
    out = io.StringIO()
    code = terms.main(argv, stdout=out, runtime_factory=factory)
    lines = out.getvalue().splitlines()
    return code, json.loads(lines[0]), len(lines)


class TermsTests(unittest.TestCase):
    def test_terms_keep_order_and_one_bad_term_does_not_hide_others(self):
        code, result, lines = run(["--model-root", "models", " 仙侠  剧 ", "坏", "美食"], FakeRuntime)
        self.assertEqual((code, lines), (0, 1))
        self.assertEqual(result, {"ok": True, "items": ["vi:仙侠 剧", "", "vi:美食"]})
        self.assertEqual(FakeRuntime.created[-1][1:], (Path("models"), "zh"))

    def test_runtime_failures_are_reported_as_json(self):
        code, result, _ = run(["--model-root", "models", "仙侠"], BrokenRuntime)
        self.assertEqual(code, 0)
        self.assertEqual(result["code"], "TRANSLATION_PACKAGE_MISSING")

    def test_input_is_bounded_and_engine_validated(self):
        code, result, _ = run(["--model-root", "m", *(["长" * 200] * 80)], FakeRuntime)
        self.assertEqual(len(result["items"]), terms.MAX_TERMS)
        self.assertEqual(len(result["items"][0]), len("vi:") + terms.MAX_TERM_CHARS)
        with self.assertRaises(SystemExit):
            terms.main(["--model-root", "m", "--engine", "gemini", "x"], stdout=io.StringIO())

    def test_engine_selects_the_runtime(self):
        chosen = []
        original = terms.create_runtime
        terms.create_runtime = lambda engine, app, models, language: chosen.append(engine) or FakeRuntime(app, models, language)
        try:
            terms.main(["--model-root", "m", "--engine", "hachimi", "x"], stdout=io.StringIO())
            terms.main(["--model-root", "m", "x"], stdout=io.StringIO())
        finally:
            terms.create_runtime = original
        self.assertEqual(chosen, ["hachimi", "argos"])


if __name__ == "__main__":
    unittest.main()

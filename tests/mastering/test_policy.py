"""Workflow policy (ADR-0052 §4): what each workflow may do at each level."""
from __future__ import annotations

import unittest

from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import policy as p


def plan_of(workflow: str, options: m.Options, segments: list[m.Segment], duration: int | None = None) -> m.Plan:
    total = duration if duration is not None else segments[-1].timeline.end
    return m.Plan("p", workflow, options, total, (m.Source("v", 600_000, 1920, 1080, 30, 1, True),), tuple(segments),
                  m.Provenance("test", "1", "r"))


def piece(index: int, start: int, end: int, at: int, **extra) -> m.Segment:
    return m.Segment(f"s{index}", "v", m.Span(start, end), m.Span(at, at + end - start), **extra)


ZOOM = m.Framing(1000, 1060)


class TableTests(unittest.TestCase):
    def test_off_allows_nothing_for_every_workflow_and_style(self):
        for workflow in m.WORKFLOWS:
            for style in m.STYLES:
                policy = p.resolve(workflow, m.Options("off", style))
                self.assertFalse(policy.enabled)
                self.assertEqual(policy.max_zoom, 1000)
                self.assertEqual(policy.hook, "off")
                self.assertTrue(all(not policy.allows(op) for op in p.OPERATIONS))

    def test_every_workflow_and_level_resolves_inside_the_contract(self):
        for workflow in m.WORKFLOWS:
            for level in m.LEVELS[1:]:
                policy = p.resolve(workflow, m.Options(level))
                with self.subTest(workflow=workflow, level=level):
                    self.assertTrue(policy.enabled)
                    self.assertLessEqual(policy.operations, set(p.OPERATIONS))
                    self.assertIn(policy.audio, p.AUDIO_POLICIES)
                    self.assertIn(policy.captions, p.CAPTION_STYLES)
                    self.assertIn(policy.hook, p.HOOK_MODES)
                    self.assertGreaterEqual(policy.max_zoom, m.MIN_ZOOM_MILLI)
                    self.assertLessEqual(policy.max_zoom, 1250)  # a gentle punch-in, never a crop that loses the subject
                    # The parallel export needs time it can cut in: at most half of the video may zoom.
                    self.assertLessEqual(policy.max_zoom_share, 500)
                    self.assertEqual(policy.allows(p.ZOOM), policy.max_zoom > 1000)

    def test_levels_only_add_operations(self):
        for workflow in m.WORKFLOWS:
            light, balanced, dynamic = (p.resolve(workflow, m.Options(level)).operations for level in ("light", "balanced", "dynamic"))
            self.assertLessEqual(light, balanced, workflow)
            self.assertLessEqual(balanced, dynamic, workflow)

    def test_dubbing_keeps_content_order_and_speech(self):
        for level in ("light", "balanced", "dynamic"):
            policy = p.resolve("dubbing_preserve", m.Options(level))
            self.assertFalse(policy.allows(p.REORDER))
            self.assertFalse(policy.allows(p.SHOT_AWARE_CUTS))
            self.assertTrue(policy.keep_source_audio)
            self.assertEqual(policy.audio, "preserve_ambience")
        self.assertEqual(p.resolve("dubbing_preserve", m.Options("light")).operations, {p.LOUDNESS, p.COLOR_MATCH})
        self.assertEqual(p.resolve("dubbing_preserve", m.Options("balanced")).hook, "off")
        self.assertEqual(p.resolve("dubbing_preserve", m.Options("dynamic")).hook, "suggest")

    def test_compilation_keeps_its_ranking_and_product_uses_the_voice_over(self):
        for level in ("light", "balanced", "dynamic"):
            self.assertFalse(p.resolve("compilation_entertainment", m.Options(level)).allows(p.REORDER))
            self.assertTrue(p.resolve("compilation_entertainment", m.Options(level)).keep_source_audio)
            product = p.resolve("product_showcase", m.Options(level))
            self.assertEqual(product.audio, "product_voiceover")
            self.assertTrue(product.allows(p.SHOT_AWARE_CUTS))

    def test_style_tunes_pacing_but_grants_nothing(self):
        natural = p.resolve("product_showcase", m.Options("balanced", "natural"))
        sports = p.resolve("product_showcase", m.Options("balanced", "sports"))
        self.assertEqual(natural.operations, sports.operations)
        self.assertEqual(natural.max_zoom, sports.max_zoom)
        self.assertLess(sports.pacing.target, natural.pacing.target)
        for style in m.STYLES:
            for level in m.LEVELS:
                pace = p.pacing(style, level)
                self.assertLessEqual(pace.minimum, pace.target)
                self.assertLessEqual(pace.target, pace.maximum)
        self.assertLess(p.pacing("natural", "dynamic").target, p.pacing("natural", "light").target)

    def test_no_policy_can_name_a_misleading_operation(self):
        for name in ("flip", "flip_h", "rotate", "stretch", "export_zoom", "speed_up"):
            self.assertNotIn(name, p.OPERATIONS)
        with self.assertRaises(ValueError):
            p.resolve("product_showcase", m.Options("light")).allows("flip_h")
        with self.assertRaises(ValueError):
            p.resolve("tiktok", m.Options("light"))

    def test_describe_lists_every_combination(self):
        table = p.describe()
        self.assertEqual(len([key for key in table if "/" in key]), len(m.WORKFLOWS) * len(m.LEVELS))
        self.assertEqual(set(table["styles"]), set(m.STYLES))


class CheckTests(unittest.TestCase):
    def test_an_allowed_product_plan_passes(self):
        options = m.Options("balanced")
        policy = p.resolve("product_showcase", options)
        segments = [piece(0, 9000, 11000, 0, role="hook", audio_mode="narration"),
                    piece(1, 0, 3000, 2000, audio_mode="narration", framing=ZOOM),
                    piece(2, 4000, 7000, 5000, audio_mode="narration")]
        self.assertEqual(p.check(plan_of("product_showcase", options, segments), policy), [])

    def test_off_never_has_a_plan(self):
        options = m.Options("off")
        problems = p.check(plan_of("product_showcase", options, [piece(0, 0, 1000, 0)]), p.resolve("product_showcase", options))
        self.assertTrue(problems)

    def test_too_much_zoom_and_too_many_zoomed_segments_are_caught(self):
        options = m.Options("balanced")
        policy = p.resolve("product_showcase", options)  # 1150, 40 %
        far = [piece(0, 0, 1000, 0, audio_mode="narration", framing=m.Framing(1000, 1300))]
        self.assertTrue(any("exceeds" in item for item in p.check(plan_of("product_showcase", options, far), policy)))
        many = [piece(i, i * 1000, i * 1000 + 1000, i * 1000, audio_mode="narration", framing=ZOOM if i < 3 else None) for i in range(5)]
        self.assertTrue(any("of the video zooms" in item for item in p.check(plan_of("product_showcase", options, many), policy)))

    def test_dubbing_plans_keep_order_and_footage(self):
        options = m.Options("balanced")
        policy = p.resolve("dubbing_preserve", options)
        whole = [piece(0, 0, 8000, 0), piece(1, 8000, 9000, 8000, framing=ZOOM)]
        self.assertEqual(p.check(plan_of("dubbing_preserve", options, whole), policy), [])
        swapped = [piece(0, 4000, 9000, 0), piece(1, 0, 4000, 5000)]
        self.assertTrue(any("out of order" in item for item in p.check(plan_of("dubbing_preserve", options, swapped), policy)))
        dropped = [piece(0, 0, 4000, 0), piece(1, 5000, 9000, 4000)]
        self.assertTrue(any("leave out footage" in item for item in p.check(plan_of("dubbing_preserve", options, dropped), policy)))
        hooked = [piece(0, 6000, 8000, 0, role="hook"), piece(1, 0, 9000, 2000)]
        self.assertTrue(any("hook is not allowed" in item for item in p.check(plan_of("dubbing_preserve", options, hooked), policy)))

    def test_a_dubbing_hook_is_only_ever_a_suggestion_for_review(self):
        options = m.Options("dynamic")
        policy = p.resolve("dubbing_preserve", options)
        silent = [piece(0, 6000, 8000, 0, role="hook"), piece(1, 0, 9000, 2000)]
        self.assertTrue(any("marked for review" in item for item in p.check(plan_of("dubbing_preserve", options, silent), policy)))
        flagged = [piece(0, 6000, 8000, 0, role="hook", needs_review=True), piece(1, 0, 9000, 2000)]
        self.assertEqual(p.check(plan_of("dubbing_preserve", options, flagged), policy), [])
        long = [piece(0, 0, 6000, 0, role="hook", needs_review=True), piece(1, 0, 9000, 6000)]
        self.assertTrue(any("longer than" in item for item in p.check(plan_of("dubbing_preserve", options, long), policy)))

    def test_a_product_plan_cannot_play_the_source_sound_over_the_voice_over(self):
        options = m.Options("light")
        loud = [piece(0, 0, 1000, 0, audio_mode="source")]
        self.assertTrue(p.check(plan_of("product_showcase", options, loud), p.resolve("product_showcase", options)))

    def test_a_plan_for_other_options_is_refused(self):
        plan = plan_of("product_showcase", m.Options("light"), [piece(0, 0, 1000, 0, audio_mode="narration")])
        self.assertTrue(p.check(plan, p.resolve("product_showcase", m.Options("dynamic"))))


if __name__ == "__main__":
    unittest.main()

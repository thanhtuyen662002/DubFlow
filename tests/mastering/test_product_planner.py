"""Bán hàng with mastering on: shot-aware parts through the existing renderer; off is unchanged (ADR-0052)."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering.planners import product as planner
from engine.dubflow.product import job, render

BALANCED = m.Options("balanced")


def analysis(duration: int, cuts: list[int], audio: bool = True) -> scene.Analysis:
    bounds = [0, *cuts, duration]
    shots = tuple(scene.Shot(a, b, 50) for a, b in zip(bounds, bounds[1:]))
    return scene.Analysis(duration, tuple(cuts), shots, (), audio, "id")


def inside_one_shot(piece: m.Segment, found: scene.Analysis) -> bool:
    return any(shot.start_ms <= piece.source.start and piece.source.end <= shot.end_ms for shot in found.shots)


class PlannerTests(unittest.TestCase):
    def test_parts_stay_inside_shots_take_turns_and_fill_the_narration(self):
        a = analysis(20_000, [3000, 9000, 15_000])
        b = analysis(8000, [5000])
        visuals = [planner.Visual(Path("a.mp4"), 20_000, 1080, 1920, a), planner.Visual(Path("b.mp4"), 8000, 1920, 1080, b),
                   planner.Visual(Path("c.jpg"), None, 1200, 1200)]
        for style in m.STYLES:
            with self.subTest(style=style):
                options = m.Options("balanced", style)
                made = planner.plan(visuals, 31_000, options, inputs={"x": "a" * 64})
                self.assertEqual(made.duration, 31_000)
                self.assertEqual(rules.check(made, rules.resolve("product_showcase", options)), [])
                self.assertEqual([s.source_id for s in made.segments[:3]], ["v1", "v2", "v3"])  # the seller's order, in turn
                for segment in made.segments:
                    if segment.source_id == "v1":
                        self.assertTrue(inside_one_shot(segment, a), segment)
                    elif segment.source_id == "v2":
                        self.assertTrue(inside_one_shot(segment, b), segment)
                    else:
                        self.assertEqual(segment.role, "still")
                    self.assertEqual(segment.audio_mode, "narration")
                    self.assertIsNone(segment.framing)  # Phase 1 decides cuts only
                # Cut margins: no part starts or ends right on a source cut.
                for segment in made.segments:
                    source = a if segment.source_id == "v1" else b if segment.source_id == "v2" else None
                    for cut in source.cuts_ms if source else ():
                        self.assertFalse(cut - planner.SHOT_MARGIN_MS < segment.source.start < cut + planner.SHOT_MARGIN_MS)
                m.Plan.from_dict(json.loads(made.to_json()))

    def test_the_legacy_plan_would_cross_cuts_the_new_one_does_not(self):
        a = analysis(12_000, [2500, 6000])
        legacy = render.plan([render.Visual(Path("a.mp4"), 12_000)], 12_000)
        crossing = [s for s in legacy if any(s.offset_ms < cut < s.offset_ms + s.duration_ms for cut in a.cuts_ms)]
        self.assertTrue(crossing)  # what Phase 1 fixes
        made = planner.plan([planner.Visual(Path("a.mp4"), 12_000, 1080, 1920, a)], 12_000, BALANCED, inputs={})
        self.assertTrue(all(inside_one_shot(segment, a) for segment in made.segments))

    def test_one_long_shot_repeats_from_where_it_stopped_and_is_flagged(self):
        a = analysis(5000, [])
        made = planner.plan([planner.Visual(Path("a.mp4"), 5000, 1080, 1920, a)], 12_000, BALANCED, inputs={})
        self.assertEqual(made.segments[0].source.start, 0)
        self.assertEqual(made.segments[1].source.start, made.segments[0].source.end % 5000)
        self.assertTrue(any(s.needs_review for s in made.segments[1:]))

    def test_tiny_shots_are_used_only_when_nothing_else_is_left(self):
        a = analysis(3000, [400, 800, 1200, 1600, 2000, 2400, 2800])
        made = planner.plan([planner.Visual(Path("a.mp4"), 3000, 1080, 1920, a)], 4000, BALANCED, inputs={})
        self.assertEqual(made.duration, 4000)
        self.assertTrue(made.warnings)

    def test_off_has_no_plan(self):
        with self.assertRaises(ValueError):
            planner.plan([planner.Visual(Path("a.jpg"), None, 1, 1)], 1000, m.OFF, inputs={})

    def test_pieces_are_the_renderer_milliseconds(self):
        a = analysis(20_000, [9000])
        made = planner.plan([planner.Visual(Path("a.mp4"), 20_000, 1080, 1920, a), planner.Visual(Path("b.jpg"), None, 1, 1)],
                            9000, BALANCED, inputs={})
        pieces = planner.pieces(made)
        self.assertEqual(sum(p.duration_ms for p in pieces), 9000)
        self.assertEqual(pieces[1].offset_ms, 0)


class FakeSpeaker:
    sample_rate = 48000

    def speak(self, text):
        return [0.1] * (48 * 400 * len(text.split()))  # 0.4 s a word

    def close(self):
        pass


class JobTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.clip = self.root / "clip.mp4"
        self.clip.write_bytes(b"mp4")
        self.photo = self.root / "photo.jpg"
        self.photo.write_bytes(b"jpg")

    def tearDown(self):
        self.folder.cleanup()

    def spec(self, **extra) -> job.Spec:
        value = {"schema_version": 1, "name": "Nồi chiên", "price": "299.000đ", "voice_id": "vi-test",
                 "variants": ["Nồi chiên không dầu dung tích lớn nấu cho cả nhà. Mua ngay hôm nay để nhận quà tặng hấp dẫn."],
                 "visuals": [str(self.clip), str(self.photo)], **extra}
        path = self.root / "spec.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return job.load_spec(path)

    def render(self, spec: job.Spec, output: str, **kwargs) -> tuple[dict, list]:
        commands: list = []

        def ffmpeg(argv, cwd, cancelled):
            commands.append(argv)
            Path(cwd, argv[-1]).write_bytes(b"mp4") if not Path(argv[-1]).is_absolute() else Path(argv[-1]).write_bytes(b"mp4")
        info = scene.StreamInfo(20_000, 1080, 1920, 30, True)
        result = job.run(spec, self.root / output, speaker=lambda status: FakeSpeaker(),
                         probe=lambda path: 20_000, measure=lambda path: (1200, 1200), ffmpeg=Path("ffmpeg"),
                         ffmpeg_runner=ffmpeg, inspect=lambda path: info, **kwargs)
        return result, commands

    def test_without_mastering_or_with_off_the_command_is_exactly_the_old_one(self):
        plain, plain_commands = self.render(self.spec(), "a")
        off, off_commands = self.render(self.spec(mastering={"schema_version": 1, "level": "off"}), "b")
        self.assertIsNone(self.spec().mastering)
        self.assertIsNone(self.spec(mastering={"schema_version": 1, "level": "off"}).mastering)
        normalise = lambda argv: [part.replace(str(self.root / "b"), str(self.root / "a")) for part in argv]
        self.assertEqual(len(plain_commands), 1)
        self.assertEqual(plain_commands[0], normalise(off_commands[0]))
        self.assertNotIn("mastering", plain["videos"][0])
        self.assertFalse(list((self.root / "a" / ".dubflow-work").glob("mastering*")))

    def test_bad_mastering_options_are_a_spec_error(self):
        with self.assertRaises(job.ProductError):
            self.spec(mastering={"schema_version": 1, "level": "max"})

    def test_balanced_uses_shot_aware_parts_saves_the_plan_and_reuses_it(self):
        found = analysis(20_000, [2500, 6000, 13_000])
        with mock.patch.object(scene, "analyse", return_value=found) as analyse:
            result, commands = self.render(self.spec(mastering={"schema_version": 1, "level": "balanced"}), "m")
            self.assertEqual(result["state"], "COMPLETED")
            report = result["videos"][0]["mastering"]
            self.assertEqual(report["level"], "balanced")
            self.assertFalse(report["reused"])
            self.assertEqual(analyse.call_count, 1)  # the photo is not analysed
            saved = m.load(self.root / "m" / ".dubflow-work" / report["plan"])
            self.assertEqual(saved.workflow, "product_showcase")
            for segment in saved.segments:
                if segment.source_id == "v1":
                    self.assertTrue(inside_one_shot(segment, found))
            # The render is the product renderer's own command, fed with the planned parts.
            argv = commands[-1]
            starts = [float(argv[i + 1]) for i, part in enumerate(argv) if part == "-ss"]
            self.assertEqual(starts, [m.to_milliseconds(s.source.start, saved.time_base) / 1000
                                      for s in saved.segments if s.source_id == "v1"])
            again, _ = self.render(self.spec(mastering={"schema_version": 1, "level": "balanced"}), "m")
            self.assertTrue(again["videos"][0]["mastering"]["reused"])
            self.assertEqual(analyse.call_count, 1)  # loaded, not analysed again

    def test_an_analysis_failure_falls_back_to_the_usual_parts(self):
        with mock.patch.object(scene, "analyse", side_effect=scene.AnalysisError("x")):
            result, commands = self.render(self.spec(mastering={"schema_version": 1, "level": "dynamic"}), "f")
        plain, plain_commands = self.render(self.spec(), "g")
        self.assertEqual(result["state"], "COMPLETED")
        self.assertIn("fallback", result["videos"][0]["mastering"])
        # a plain join is never shown as reviewed footage: the result says so (real run 8, P2)
        self.assertTrue(result["videos"][0]["mastering"]["needs_review"])
        self.assertIn(result["videos"][0]["mastering"]["fallback"], result["videos"][0]["mastering"]["warnings"])
        normalise = lambda argv: [part.replace(str(self.root / "f"), str(self.root / "g")) for part in argv]
        self.assertEqual(normalise(commands[-1]), plain_commands[-1])

    def test_a_failed_ffmpeg_analysis_falls_back(self):
        result, _ = self.render(self.spec(mastering={"schema_version": 1, "level": "light"}), "n")
        self.assertIn("fallback", result["videos"][0]["mastering"])
        self.assertEqual(result["state"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()

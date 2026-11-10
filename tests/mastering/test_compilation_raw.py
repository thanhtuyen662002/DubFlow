"""Tổng hợp from un-dubbed footage: v2 programme, per-part plans, montage render (ADR-0053)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.compilation import job, program, render
from engine.dubflow.compilation.raw import resolve, run_program
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import plan as m

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")


def _can_make_webm() -> bool:
    if not FFMPEG:
        return False
    try:
        listed = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "libsvtav1" in listed and "libopus" in listed


WEBM = _can_make_webm()


class FakeSpeaker:
    sample_rate = 48000

    def speak(self, text):
        return [0.05] * (48 * 280 * len(text.split()))

    def close(self):
        pass


def write_spec(root: Path, value: dict) -> Path:
    path = root / "spec.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.video = self.root / "raw.mov"
        self.video.write_bytes(b"x")
        (self.root / "captions_vi.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chào\n", encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def base(self, **extra):
        items = [{"title": f"Mục {i}", "narration": "Một câu.", "sources": [{"video": str(self.video)}]} for i in (1, 2)]
        return {"schema_version": 2, "title": "Top 2", "layout": "toan-man-hinh", "voice_id": "v", "items": items, **extra}

    def test_v2_accepts_raw_videos_finds_queue_subtitles_and_defaults_to_a_plan(self):
        spec = job.load_spec(write_spec(self.root, self.base()))
        self.assertIsInstance(spec, program.Program)
        self.assertEqual(spec.items[0].sources[0].transcript, self.root / "captions_vi.srt")
        self.assertEqual(spec.mastering.level, "balanced")
        off = job.load_spec(write_spec(self.root, self.base(mastering={"schema_version": 1, "level": "off"})))
        self.assertEqual(off.mastering.level, "light")

    def test_v1_still_requires_dubbed_videos(self):
        value = self.base()
        value["schema_version"] = 1
        with self.assertRaises(job.CompilationError):
            job.load_spec(write_spec(self.root, value))

    def test_bad_programmes_are_refused(self):
        for change in ({"program": {"kind": "best"}}, {"items": []}):
            with self.subTest(change=change), self.assertRaises(job.CompilationError):
                job.load_spec(write_spec(self.root, self.base(**change)))
        value = self.base()
        value["items"][0]["rank"] = value["items"][1]["rank"] = 3
        with self.assertRaises(job.CompilationError):
            job.load_spec(write_spec(self.root, value))
        value = self.base()
        value["items"][0]["sources"][0]["rights"] = "stolen"
        with self.assertRaises(job.CompilationError):
            job.load_spec(write_spec(self.root, value))

    def test_ranking_is_the_users_unless_a_suggestion_is_applied(self):
        value = self.base(program={"kind": "top_n", "ranking": "suggested"})
        spec = job.load_spec(write_spec(self.root, value))
        warnings = []
        kept, record = resolve(spec, None, warnings)
        self.assertEqual([i.item_id for i in kept.items], ["i1", "i2"])
        self.assertTrue(warnings)
        ask = lambda system, schema, parts: {"order": ["i2", "i1"], "reasons": {"i2": "bất ngờ hơn", "i1": "nhẹ nhàng"}}
        kept, record = resolve(spec, ask, [])
        self.assertEqual([i.item_id for i in kept.items], ["i1", "i2"])  # suggested, not applied
        self.assertEqual(record["suggestion"]["order"], ["i2", "i1"])
        value["program"]["apply_suggestion"] = True
        applied, _ = resolve(job.load_spec(write_spec(self.root, value)), ask, [])
        self.assertEqual([(i.item_id, i.rank) for i in applied.items], [("i2", 2), ("i1", 1)])

    def test_missing_narration_is_drafted_from_the_items_own_facts_and_flagged(self):
        value = self.base(editorial={"ai": {"provider": "gemini"}})
        value["items"][0]["narration"] = ""
        spec = job.load_spec(write_spec(self.root, value))
        with self.assertRaises(job.CompilationError):
            resolve(spec, None, [])
        seen = []

        def ask(system, schema, parts):
            seen.append(parts[0]["text"])
            return {"narration": "Một chú mèo chào mọi người.", "needs_check": ["tên con mèo"]}
        warnings = []
        drafted, record = resolve(spec, ask, warnings)
        self.assertEqual(drafted.items[0].narration, "Một chú mèo chào mọi người.")
        self.assertIn("tên con mèo", drafted.items[0].verify)
        self.assertIn("xin chào", seen[0])  # the queue subtitles were given as facts
        self.assertTrue(any("AI viết nháp" in w for w in warnings))


@unittest.skipUnless(FFMPEG and FFPROBE, "needs FFmpeg")
class RealMediaProgrammeTest(unittest.TestCase):
    """Five raw sources with subtitles: a Top 5 with hook, intro, items, outro, captions and source moments."""

    def make(self, path: Path, pattern: str, seconds: int, *, audio: str | None, size: str = "640x360") -> None:
        webm = path.suffix == ".webm"  # AV1 + Opus, as a downloaded WebM
        command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=s={size}:r=25:d={seconds}"]
        if audio:
            command += ["-f", "lavfi", "-i", f"{audio}:d={seconds}", "-c:a", "libopus" if webm else "aac", "-shortest"]
        command += ["-c:v", "libsvtav1", "-preset", "12"] if webm else ["-c:v", "mpeg4", "-q:v", "5"]
        command.append(str(path))
        subprocess.run(command, check=True, timeout=120)

    def test_top5_from_raw_footage(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            topics = [("meo", "con mèo nhảy lên bàn", "testsrc2", "sine=f=440"), ("cho", "chú chó bơi qua sông", "smptebars", "sine=f=660"),
                      ("xe", "chiếc xe đạp tự chạy", "rgbtestsrc", None), ("banh", "quả bóng bay qua tường", "testsrc", "sine=f=880"),
                      ("ga", "con gà gáy buổi sáng", "yuvtestsrc", "sine=f=300")]
            items = []
            for number, (name, said, pattern, audio) in enumerate(topics, 1):
                clip = root / (f"{name}.webm" if name == "banh" and WEBM else f"{name}.mp4")
                self.make(clip, pattern, 9, audio=audio, size="360x640" if number == 2 else "640x360")
                srt = root / f"{name}.srt"
                srt.write_text(f"1\n00:00:04,000 --> 00:00:07,000\n{said}\n", encoding="utf-8")
                items.append({"title": said.capitalize(), "narration": f"Đây là {said}. Thật khó tin phải không.",
                              "sources": [{"video": str(clip), "transcript": str(srt), "rights": "owned",
                                           "credit": {"creator": "Tác giả thử", "platform": "máy", "license": "CC0"}}]})
            value = {"schema_version": 2, "title": "Top 5 điều kỳ lạ", "layout": "toan-man-hinh", "voice_id": "v",
                     "program": {"kind": "top_n"}, "hook": "Bạn sẽ không tin chiếc xe đạp tự chạy.", "intro": "Cùng xem năm điều kỳ lạ nhất.",
                     "outro": "Cảm ơn bạn đã xem.", "items": items}
            items[4]["narration"] = ""  # drafted by the AI
            value["editorial"] = {"ai": {"provider": "gemini"}}
            spec = job.load_spec(write_spec(root, value))
            out = root / "out"
            drafts = []

            def ask_text(system, schema, parts):
                drafts.append(parts[0]["text"])
                return {"narration": f"Một con gà gáy vang lần thứ {len(drafts)}.", "needs_check": []}

            class Seeing:  # a describer whose answers are cached: the second run asks for nothing
                provider, used_model, warnings = "gemini", "fake", []
                runs = 0

                def __init__(self, cache, context="", labels=()):
                    Seeing.runs += 1
                    self.asked = 0

                def describe(self, frames, cancelled):
                    self.asked = len(frames) if Seeing.runs == 1 else 0
                    return {}

            def probe(path):
                from engine.dubflow.product.job import probe_duration_ms
                return probe_duration_ms(Path(FFPROBE), path)
            commands = []

            def runner(argv, cwd, cancelled):
                commands.append(argv)
                from engine.dubflow.product.job import run_ffmpeg
                run_ffmpeg(argv, cwd, cancelled)
            result = run_program(spec, out, speaker=lambda status: FakeSpeaker(), probe=probe,
                                 inspect=lambda path: scene.probe(Path(FFPROBE), path), ffmpeg=Path(FFMPEG),
                                 ffmpeg_runner=runner, encoder="mpeg4", vision=Seeing, ask_text=ask_text)
            self.assertEqual(result["state"], "COMPLETED", result)
            work = out / ".dubflow-work"
            hook = m.load(work / "mastering" / "plan-hook.json")
            self.assertEqual(hook.segments[0].role, "hook")
            self.assertEqual(hook.segments[0].source_id, "i3-s1")  # the hook line's words are in source 3's subtitles
            for part in ("i1", "i2", "i4", "i5"):  # every item with sound ends on its own sound
                item = m.load(work / "mastering" / f"plan-{part}.json")
                self.assertEqual(item.segments[-1].audio_mode, "source", part)
                self.assertFalse(any(s.role == "still" for s in item.segments), part)  # the narration had footage ahead
            if WEBM:  # the WebM is played from a stream copy in Matroska
                self.assertTrue([c for c in commands if "copy" in c and str(c[-1]).endswith(".partial.mkv")])
            silent = m.load(work / "mastering" / "plan-i3.json")
            self.assertFalse(any(s.audio_mode == "source" for s in silent.segments))  # no audio: no moment
            for plan_file in (work / "mastering").glob("plan-*.json"):
                saved = m.load(plan_file)
                ends: dict = {}
                for s in saved.segments:
                    if s.role not in ("hook", "still"):
                        self.assertGreaterEqual(s.source.start, ends.get(s.source_id, 0))  # each source plays forward
                        ends[s.source_id] = s.source.end
            chapters = (out / "chapters.txt").read_text(encoding="utf-8")
            self.assertIn("#5 Con mèo nhảy lên bàn", chapters)
            self.assertIn("Mở đầu", chapters)
            self.assertIn("CC0", (out / "description.txt").read_text(encoding="utf-8"))
            self.assertIn("chiếc xe đạp", (out / "captions.srt").read_text(encoding="utf-8"))
            programme = json.loads((work / "program.json").read_text(encoding="utf-8"))
            self.assertEqual([p["id"] for p in programme["parts"]], ["hook", "intro", "i1", "i2", "i3", "i4", "i5", "outro"])
            renders = [c for c in commands if "-filter_complex" in c and "concat=n=" in " ".join(c) and str(c[-1]).endswith(".mp4")]
            self.assertEqual(len(renders), 9)  # 8 parts + the join: one encode each, like v1
            self.assertEqual(len(drafts), 1)
            first_draft = m.load(work / "mastering" / "plan-i5.json")
            # Edit one item: only that part is rendered again.
            value["items"][1]["narration"] = "Một chú chó bơi rất giỏi."
            commands.clear()
            again = run_program(job.load_spec(write_spec(root, value)), out, speaker=lambda status: FakeSpeaker(), probe=probe,
                                inspect=lambda path: scene.probe(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), ffmpeg_runner=runner,
                                encoder="mpeg4", vision=Seeing, ask_text=ask_text)
            self.assertEqual(again["state"], "COMPLETED", again)
            parts = [c[-1] for c in commands if str(c[-1]).endswith(".partial.mp4") and "part-" in str(c[-1])]
            self.assertEqual([Path(p).name for p in parts], ["part-i2.partial.mp4"])
            self.assertFalse([c for c in commands if "scdet" in " ".join(c)])  # sources not analysed again
            self.assertEqual(len(drafts), 1)  # the AI's draft was kept, not written again
            self.assertEqual(m.load(work / "mastering" / "plan-i5.json"), first_draft)


class MomentTest(unittest.TestCase):
    def test_the_ending_is_kept_for_the_items_own_sound(self):
        from engine.dubflow.mastering import candidates as shots, evidence as ev
        from engine.dubflow.mastering.planners import compilation as planner
        found = scene.Analysis(12_000, (6000,), (scene.Shot(0, 6000, 40), scene.Shot(6000, 12_000, 60)), (), True, "x" * 64)
        source = planner.Footage("i1-s1", Path("meo.mp4"), found, 640, 360, (25, 1),
                                 cues=((7000, 9000, "con mèo nhảy lên bàn"),))
        quality = shots.Quality(120, 50, 300)
        pool = [shots.Candidate("i1-s1c1", "i1-s1", 0, 6000, 0, 40, quality, 1),
                shots.Candidate("i1-s1c2", "i1-s1", 6000, 12_000, 1, 60, quality, 2**40)]
        evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
        spoken = [("Con mèo nhảy lên bàn rất nhanh.", 2500), ("Ai cũng bất ngờ.", 2500)]
        made = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                 footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                 inputs={"part": "0" * 64})
        last = made.plan.segments[-1]
        self.assertEqual((last.role, last.audio_mode), ("reaction", "source"))
        self.assertGreaterEqual(last.source.start, 6000)
        self.assertFalse(any(s.role == "still" for s in made.plan.segments))
        narration = [s for s in made.plan.segments if s.audio_mode != "source"]
        self.assertTrue(all(s.source.end <= last.source.start for s in narration))  # the source still plays forward


class MontageArgvTest(unittest.TestCase):
    def test_audio_modes_and_still(self):
        info = scene.StreamInfo(9000, 640, 360, 25, True)
        argv = render.montage_argv(Path("ffmpeg"), [(Path("a.mp4"), info, 1000, 2000, "mixed", True, False),
                                                    (Path("b.mp4"), info, 3000, 1500, "source", True, False),
                                                    (Path("c.mp4"), info, 500, 1000, "narration", True, True)],
                                   Path("n.wav"), None, 0.1, 4500, "p.ass", Path("o.mp4"), layout="toan-man-hinh")
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertIn(f"volume={render.UNDER_NARRATION:.3f}", graph)
        self.assertIn("volume=1.000", graph)
        self.assertIn("loop=loop=-1:size=1", graph)
        self.assertEqual(graph.count("anullsrc"), 1)
        for word in ("rotate", "hflip", "vflip", "zoompan", "setpts=0.", "atempo"):
            self.assertNotIn(word, graph)


if __name__ == "__main__":
    unittest.main()

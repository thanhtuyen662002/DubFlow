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
                # the quota ran out after some frames (real set v5): the part logs must say so
                provider, used_model, warnings, budget = "gemini", "fake", ["AI không xem được 4 khung hình (QUOTA)"], 60
                runs = 0

                def __init__(self, cache, context="", labels=()):
                    Seeing.runs += 1
                    self.asked = self.taken = 0

                def describe(self, frames, cancelled):
                    self.taken += len(frames)
                    self.asked += len(frames) if Seeing.runs == 1 else 0
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
            first_log = json.loads((work / "mastering" / "editorial-i1.json").read_text(encoding="utf-8"))
            self.assertEqual(first_log["vision"]["warnings"], ["AI không xem được 4 khung hình (QUOTA)"])
            self.assertIn("AI không xem được 4 khung hình (QUOTA)", first_log["warnings"])
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
            intro = m.load(work / "mastering" / "plan-intro.json")
            # the introduction shows items' footage before them: always looked at (rc5 B1, an item's key shot READY)
            self.assertTrue(intro.segments and all(s.needs_review for s in intro.segments))
            starts = [p["start_ms"] for p in programme["parts"]]
            self.assertEqual(starts[0], 0)
            self.assertEqual(starts[1:], [p["start_ms"] + p["duration_ms"] for p in programme["parts"][:-1]], "parts follow each other")
            renders = [c for c in commands if "-filter_complex" in c and "concat=n=" in " ".join(c) and str(c[-1]).endswith(".mp4")
                       and "parts.ffconcat" not in c]
            self.assertEqual(len(renders), 8)  # one encode per part; the join copies their pictures
            join = next(c for c in commands if "parts.ffconcat" in c)
            self.assertEqual(join[join.index("-c:v") + 1], "copy")  # P2-9: pictures copied, sound joined sample-exactly once
            self.assertEqual(programme["joined"], "copy")
            self.assertTrue([c for c in commands if "parts.ffconcat" in c and "copy" in c])
            streams = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json",
                                                 str(out / "compilation.mp4")], capture_output=True, text=True, timeout=60).stdout)["streams"]
            lengths = {s["codec_type"]: float(s["duration"]) for s in streams}
            self.assertLess(abs(lengths["video"] - lengths["audio"]), 0.25, lengths)  # picture and sound end together
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
            self.assertEqual(again["rendered_parts"], ["i2"])
            self.assertEqual(len(again["reused_parts"]), 7)
            self.assertIn("i2", again["review_parts"])
            # The desktop's review panel pins another shot for a sentence of item 4: the next run plans that part
            # with the pin, renders only it, and the decision is still there afterwards.
            from engine.dubflow.mastering import review as reviewing
            import contextlib
            import io
            part, unit, other = next((p, u, c["id"]) for p in ("intro", "outro", "hook")
                                     for log in [json.loads((work / "mastering" / f"editorial-{p}.json").read_text(encoding="utf-8"))]
                                     for u in log["units"] for c in log["candidates"] if c["id"] not in u["chosen"])
            shown = io.StringIO()
            with contextlib.redirect_stdout(shown):
                self.assertEqual(reviewing.main(["--work", str(work), "--part", part, "accept", "true"]), 0)
                code = reviewing.main(["--work", str(work), "--part", part, "decide", unit["unit_id"], "pin", other])
                # P2-5: Ghim → Duyệt cảnh này → Dựng lại keeps the approval
                approve = reviewing.main(["--work", str(work), "--part", part, "decide", unit["unit_id"], "approve"])
            self.assertEqual(code, 0, shown.getvalue())
            self.assertEqual(approve, 0, shown.getvalue())
            self.assertTrue((work / f"review-{part}.json").is_file())
            commands.clear()
            third = run_program(job.load_spec(write_spec(root, value)), out, speaker=lambda status: FakeSpeaker(), probe=probe,
                                inspect=lambda path: scene.probe(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), ffmpeg_runner=runner,
                                encoder="mpeg4", vision=Seeing, ask_text=ask_text)
            self.assertEqual(third["state"], "COMPLETED", third)
            self.assertEqual(third["rendered_parts"], [part])
            pinned = next(u for u in json.loads((work / "mastering" / f"editorial-{part}.json").read_text(encoding="utf-8"))["units"]
                          if u["unit_id"] == unit["unit_id"])
            self.assertEqual(pinned["chosen"][0], other)
            kept = reviewing.load(work / f"review-{part}.json")
            self.assertEqual(kept.units[unit["unit_id"]].pin, [other])
            self.assertFalse(kept.units[unit["unit_id"]].invalidated)
            self.assertEqual(reviewing.user_state(kept.units[unit["unit_id"]]), reviewing.USER_APPROVED)
            self.assertFalse([w for w in third.get("warnings", []) if "đã đổi sau khi bạn duyệt" in w])
            # the part changed after the user accepted to export it with unreviewed sentences: the export asks again
            self.assertFalse(kept.accept_unreviewed)
            self.assertFalse(third["review_parts"][part]["accept_unreviewed"])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                reviewing.main(["--work", str(work), "--part", "../i4", "view"])  # a part id is a name, never a path


class PartIndependenceTest(unittest.TestCase):
    """rc12 W2 (P2-N18): an exclusion in one item re-planned and re-rendered an untouched item, because the vision
    frames left for each part depended on what the parts before it had spent in that run."""

    class Describer:
        def __init__(self, budget: int, taken: int) -> None:
            self.budget, self.taken = budget, taken

    def rooms(self, spending: list[int], budget: int = 28, broad: int = 4) -> list[int]:
        """The room each part starts with when the parts before it spend ``spending``."""
        from engine.dubflow.compilation.raw import PartLooks
        describer = self.Describer(budget, broad)
        looks = PartLooks(describer, len(spending))
        out = []
        for spent in spending:
            looks.start()
            out.append(looks.room())
            describer.taken += min(spent, looks.room())
        return out

    def test_what_an_earlier_part_spends_never_changes_a_later_parts_room(self):
        # rc12: approvals in i2 kept its shots whole, so i2 looked at fewer frames; the old room of a part,
        # (budget − taken) // parts left, then grew for i3 and i4 and their plans changed
        draft = self.rooms([4, 4, 4, 4, 4, 4])
        edited = self.rooms([4, 0, 1, 4, 4, 4])
        self.assertEqual(draft, [4, 4, 4, 4, 4, 4])
        self.assertEqual(edited, draft)
        self.assertEqual(self.rooms([4, 4, 4, 4, 4, 4], budget=40), self.rooms([0, 9, 0, 0, 2, 4], budget=40))

    def test_a_part_never_spends_more_than_its_share_or_what_the_budget_has_left(self):
        from engine.dubflow.compilation.raw import PartLooks
        describer = self.Describer(28, 4)
        looks = PartLooks(describer, 6)
        looks.start()
        describer.taken += 3
        self.assertEqual(looks.room(), 1)  # share 4, three spent
        describer.taken += 5  # a look takes what it asked for, even past its share
        self.assertEqual(looks.room(), 0)
        describer.taken = 26
        looks.start()
        self.assertEqual(looks.room(), 2)  # only what the budget has left
        describer.taken = 30
        self.assertEqual(looks.room(), 0)
        self.assertEqual(PartLooks(self.Describer(10, 12), 3).share, 0)  # the broad look took more than the budget

    @unittest.skipUnless(FFMPEG and FFPROBE, "needs FFmpeg")
    def test_what_one_item_spends_never_changes_what_the_later_items_look_at(self):
        """The whole program, run twice. In the second run item 1 costs twice as many frames (in rc12 the user's
        exclusion and approvals made items 1 and 2 cost fewer). The later parts are asked exactly the same frames
        and plan exactly the same footage: with the old room, (budget − taken) // parts left, items 2–4 were asked
        fewer frames."""
        from engine.dubflow.mastering import vision as seeing
        with TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for number, pattern in enumerate(("testsrc2", "smptebars", "rgbtestsrc", "testsrc"), 1):
                clip = root / f"c{number}.mp4"
                subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=s=320x180:r=25:d=60",
                                "-f", "lavfi", "-i", "sine=f=440:d=60", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest",
                                str(clip)], check=True, timeout=120)
                items.append({"title": f"Mục {number}", "narration": f"Đây là cảnh số {number}. Quả bóng đỏ lăn qua. Và cảnh cuối.",
                              "sources": [{"video": str(clip), "rights": "owned"}]})
            value = {"schema_version": 2, "title": "Bốn mục", "layout": "toan-man-hinh", "voice_id": "v",
                     "program": {"kind": "list"}, "intro": "Cùng xem.", "outro": "Hết.", "items": items,
                     "editorial": {"ai": {"provider": "gemini"}}}
            asked: list[list[str]] = []
            costly = {"i1": False}

            class Seeing:  # answers every frame; only the extra frames of a long take show what a sentence says
                provider, used_model, warnings, budget = "gemini", "fake", [], 44  # less than the parts would take

                def __init__(self, cache, context="", labels=()):
                    self.asked = self.taken = 0

                def describe(self, frames, cancelled):
                    ids = [cid for cid, _ in frames]
                    asked.append(ids)
                    heavy = costly["i1"] and all(cid.startswith("i1-") for cid in ids)
                    self.taken += len(ids) * (2 if heavy else 1)
                    return {cid: seeing.Seen("quả bóng đỏ lăn" if "@" in cid else "một khung hình màu", (), (), "", (), 600, "clear",
                                             None, None, 0) for cid in ids}

            def run():
                return run_program(job.load_spec(write_spec(root, value)), root / "out", speaker=lambda status: FakeSpeaker(),
                                   probe=lambda path: __import__("engine.dubflow.product.job", fromlist=["x"]).probe_duration_ms(Path(FFPROBE), path),
                                   inspect=lambda path: scene.probe(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4", vision=Seeing)
            first = run()
            self.assertEqual(first["state"], "COMPLETED", first)
            work = root / "out" / ".dubflow-work" / "mastering"
            plans = {p.name: p.read_bytes() for p in work.glob("plan-*.json")}
            draft, asked[:] = list(asked), []
            costly["i1"] = True
            again = run()
            self.assertEqual(again["state"], "COMPLETED", again)
            self.assertTrue(any(all(cid.startswith("i1-") for cid in ids) for ids in draft), draft)  # item 1 did look
            self.assertTrue(any(all(cid.startswith(("i2-", "i3-", "i4-")) for cid in ids) for ids in draft), draft)
            self.assertEqual(asked, draft)  # every look of every part, in order
            self.assertEqual(again["rendered_parts"], [], again)
            for name, data in plans.items():
                self.assertEqual((work / name).read_bytes(), data, name)


    @unittest.skipUnless(FFMPEG and FFPROBE, "needs FFmpeg")
    def test_an_edit_in_the_intro_never_changes_what_an_item_sees(self):
        """rc14 W5 on W2 (P2-N22, #796): approvals in the intro locked its shots, so its next multi-frame look went to
        another shot of item 1 and split it; item 1 was re-planned and re-rendered without being touched, and lost
        its source-audio moment. The intro, hook and outro look at the items' footage for themselves only."""
        from engine.dubflow.mastering import review as reviewing
        from engine.dubflow.mastering import vision as seeing
        import contextlib
        import io
        with TemporaryDirectory() as folder:
            root = Path(folder)
            items = []
            for number, pattern in enumerate(("testsrc2", "smptebars", "rgbtestsrc", "testsrc"), 1):
                clip = root / f"c{number}.mp4"
                subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=s=320x180:r=25:d=60",
                                "-f", "lavfi", "-i", "sine=f=440:d=60", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-shortest",
                                str(clip)], check=True, timeout=120)
                items.append({"title": f"Mục {number}", "narration": f"Đây là cảnh số {number}. Quả bóng đỏ lăn qua. Và cảnh cuối.",
                              "sources": [{"video": str(clip), "rights": "owned"}]})
            value = {"schema_version": 2, "title": "Bốn mục", "layout": "toan-man-hinh", "voice_id": "v",
                     "program": {"kind": "list"}, "intro": "Cùng xem quả bóng đỏ lăn qua. Màu xanh đẹp quá. Một ngày vui.",
                     "outro": "Hết.", "items": items, "editorial": {"ai": {"provider": "gemini"}}}

            class Seeing:  # only the extra frames of a long take show what a sentence says
                provider, used_model, warnings, budget = "gemini", "fake", [], 44

                def __init__(self, cache, context="", labels=()):
                    self.asked = self.taken = 0

                def describe(self, frames, cancelled):
                    self.taken += len(frames)
                    return {cid: seeing.Seen("quả bóng đỏ lăn" if "@" in cid else "một khung hình màu", (), (), "", (), 600, "clear",
                                             None, None, 0) for cid, _ in frames}

            def run():
                return run_program(job.load_spec(write_spec(root, value)), root / "out", speaker=lambda status: FakeSpeaker(),
                                   probe=lambda path: __import__("engine.dubflow.product.job", fromlist=["x"]).probe_duration_ms(Path(FFPROBE), path),
                                   inspect=lambda path: scene.probe(Path(FFPROBE), path), ffmpeg=Path(FFMPEG), encoder="mpeg4", vision=Seeing)
            first = run()
            self.assertEqual(first["state"], "COMPLETED", first)
            work = root / "out" / ".dubflow-work"
            items_plans = {f"plan-i{n}.json": (work / "mastering" / f"plan-i{n}.json").read_bytes() for n in range(1, 5)}
            shown = io.StringIO()
            with contextlib.redirect_stdout(shown):
                # the user edits the intro only: another shot for its first sentence, three of item 2's shots out of
                # its second
                first_id, second_id = [u["unit_id"] for u in json.loads((work / "mastering" / "editorial-intro.json")
                                                                         .read_text(encoding="utf-8"))["units"][:2]]
                self.assertEqual(reviewing.main(["--work", str(work), "--part", "intro", "decide", first_id, "pin", "i3-s1c4"]), 0,
                                 shown.getvalue())
                self.assertEqual(reviewing.main(["--work", str(work), "--part", "intro", "decide", second_id, "exclude",
                                                 "i2-s1c1", "i2-s1c2", "i2-s1c3"]), 0, shown.getvalue())
            again = run()
            self.assertEqual(again["state"], "COMPLETED", again)
            self.assertFalse(set(again["rendered_parts"]) - {"intro", "outro"}, again["rendered_parts"])
            for name, data in items_plans.items():
                self.assertEqual((work / "mastering" / name).read_bytes(), data, name)


class MomentTest(unittest.TestCase):
    def test_the_ending_is_kept_for_the_items_own_sound(self):
        from engine.dubflow.mastering import candidates as shots, evidence as ev
        from engine.dubflow.mastering.planners import compilation as planner
        found = scene.Analysis(30_000, (6000, 12_000, 18_000, 24_000),
                               tuple(scene.Shot(a, a + 6000, 50) for a in range(0, 30_000, 6000)), (), True, "x" * 64)
        source = planner.Footage("i1-s1", Path("meo.mp4"), found, 640, 360, (25, 1),
                                 cues=((7000, 9000, "con mèo nhảy lên bàn"), (25_000, 28_000, "trời ơi")))
        quality = shots.Quality(120, 50, 300)
        pool = [shots.Candidate(f"i1-s1c{n + 1}", "i1-s1", n * 6000, n * 6000 + 6000, n, 50, quality, n * 2**20 + 1)
                for n in range(5)]
        evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
        spoken = [("Con mèo nhảy lên bàn rất nhanh.", 2500), ("Ai cũng bất ngờ.", 2500)]
        made = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                 footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                 inputs={"part": "0" * 64})
        last = made.plan.segments[-1]
        self.assertEqual((last.role, last.audio_mode), ("reaction", "source"))
        self.assertGreaterEqual(last.source.start, 12_000)  # late in the source, after what the narration shows
        self.assertFalse(any(s.role == "still" for s in made.plan.segments))
        narration = [s for s in made.plan.segments if s.audio_mode != "source"]
        self.assertTrue(all(s.source.end <= last.source.start for s in narration))  # the source still plays forward


    def test_a_long_source_keeps_its_late_footage_for_the_narration(self):
        """Why We Fight (run 9): the moment was chosen first, early, and the narration only played what came before
        it; the map the narration names, 8 minutes in, was out of reach."""
        from engine.dubflow.mastering import candidates as shots
        from engine.dubflow.mastering.planners import compilation as planner
        length = 600_000
        cuts = tuple(range(6000, length, 6000))
        found = scene.Analysis(length, cuts, tuple(scene.Shot(a, a + 6000, 50) for a in range(0, length, 6000)), (), True, "y" * 64)
        source = planner.Footage("i1-s1", Path("war.mp4"), found, 640, 360, (25, 1),
                                 cues=((60_000, 63_000, "trời ơi nhìn kìa"), (480_500, 485_000, "bản đồ dãy núi Kavkaz")))
        quality = shots.Quality(120, 50, 300)
        pool = [shots.Candidate(f"i1-s1c{n + 1}", "i1-s1", n * 6000, n * 6000 + 6000, n, 50, quality, n * 2**20 + 1)
                for n in range(length // 6000)]
        evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
        spoken = [("Bản đồ dãy núi Kavkaz.", 2500), ("Nơi hai bên giao tranh.", 2500)]
        made = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                 footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Kavkaz",
                                 inputs={"part": "0" * 64})
        narration = [s for s in made.plan.segments if s.audio_mode != "source"]
        self.assertTrue(any(s.source.start < 486_000 and s.source.end > 480_000 for s in narration),
                        [(s.source.start, s.source.end) for s in narration])
        last = made.plan.segments[-1]
        self.assertEqual(last.role, "reaction")
        self.assertTrue(all(s.source.end <= last.source.start for s in narration))  # still forward

    def short_source_split_into_frames(self):
        """rc12 W2 i1 (m2-lop-hoc, 21.5 s): shots split by a multi-frame look into pieces of 1–2 s, so no moment of
        2 s is left after a narration that plays forward over most of the source."""
        from engine.dubflow.mastering import candidates as shots
        from engine.dubflow.mastering.planners import compilation as planner
        bounds = [(0, 6000), (6000, 7950), (7950, 10050), (10050, 12000), (12000, 13950), (13950, 15900), (15900, 16825),
                  (16825, 17963), (17963, 19188), (19188, 20327), (20327, 21500)]
        names = ["c1", "c2f1", "c2f2", "c2f3", "c3f1", "c3f2", "c3f3", "c4f1", "c4f2", "c4f3", "c5"]
        found = scene.Analysis(21_500, tuple(b for _, b in bounds[:-1]), tuple(scene.Shot(a, b, 50) for a, b in bounds), (),
                               True, "q" * 64)
        source = planner.Footage("i1-s1", Path("lop-hoc.webm"), found, 640, 360, (30, 1),
                                 cues=((12_100, 13_800, "cỗ máy domino đổ dây chuyền"), (6100, 7800, "học sinh xếp domino"),
                                       (8000, 10_000, "quả bóng lăn qua bàn"), (10_100, 11_900, "học sinh xếp domino")))
        quality = shots.Quality(120, 50, 300)
        pool = [shots.Candidate(f"i1-s1{n}", "i1-s1", a, b, k, 50, quality, k * 2**20 + 1)
                for k, ((a, b), n) in enumerate(zip(bounds, names))]
        return planner, source, pool, planner.evidence_for([source], {"i1-s1": pool}, {})

    def test_no_frame_is_held_to_buy_a_moment_and_an_excluded_shot_is_never_shown(self):  # P2-N17
        from engine.dubflow.mastering import align
        planner, source, pool, evidence = self.short_source_split_into_frames()
        spoken = [("Cỗ máy domino đổ dây chuyền trong lớp học.", 4465), ("Học sinh xếp domino và quả bóng lăn qua bàn.", 4625)]
        for edits in (None, align.Edits({}, {"i1-s1c1"}, set())):
            made = planner.plan_part("i1", "item", spoken, 9490, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                     footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Lớp học domino",
                                     inputs={"part": "0" * 64}, edits=edits)
            segments = made.plan.segments
            # before: 2725 ms (draft) and 7540 ms (after excluding c1, five held frames) of held frames bought a moment
            self.assertEqual([s for s in segments if s.role == "still"], [], edits)
            narration = [s for s in segments if s.audio_mode != "source"]
            self.assertEqual(sum(s.timeline.end - s.timeline.start for s in narration), 9490)
            if edits:
                self.assertTrue(all(s.source.start >= 6000 for s in segments))  # nothing of the excluded shot

    def test_excluded_opening_shots_are_never_counted_as_footage_ahead_of_the_moment(self):  # P2-N17
        """With the opening shots excluded, a moment placed as if they were still ahead of it leaves the narration
        only excluded footage ("no footage left after the edits"); counted without them, the item is planned."""
        from engine.dubflow.mastering import align
        planner, source, pool, evidence = self.short_source_split_into_frames()
        for excluded in ({"i1-s1c1", "i1-s1c2f1"}, {"i1-s1c1", "i1-s1c2f1", "i1-s1c2f2"}):
            for narration_ms in (3000, 6000):
                spoken = [("Cỗ máy domino đổ dây chuyền trong lớp học.", narration_ms // 2),
                          ("Học sinh xếp domino và quả bóng lăn qua bàn.", narration_ms - narration_ms // 2)]
                made = planner.plan_part("i1", "item", spoken, narration_ms + 300, m.Options("balanced", "entertainment"),
                                         lead_ms=200, gap_ms=300, footage=[source], candidates={"i1-s1": pool}, evidence=evidence,
                                         title="Lớp học domino", inputs={"part": "0" * 64},
                                         edits=align.Edits({}, set(excluded), set()))
                segments = made.plan.segments
                self.assertEqual([s for s in segments if s.role == "still"], [], (excluded, narration_ms))
                shown = {c.candidate_id: c for c in pool if c.candidate_id in excluded}
                self.assertFalse(any(s.source.start < c.end_ms and s.source.end > c.start_ms
                                     for s in segments for c in shown.values()), (excluded, narration_ms))

    def test_a_short_shot_ahead_is_never_skipped_for_a_held_frame(self):  # P2-N17, rc13 W2 i1
        """rc13 W2: after excluding i1's opening shot the narration started at 12 s, where 9502 ms were left for
        9490 ms of narration. Filler had to be at least 1200 ms, so u2 skipped c4f1 (1138 ms) for c4f2, ran out of
        footage 1126 ms short and held a frame of c2f1. Geometry and evidence as the rc13 replay saw them."""
        from engine.dubflow.mastering import align, candidates as shots, evidence as ev
        from engine.dubflow.mastering.planners import compilation as planner
        bounds = [("c1", 0, 6000, 958), ("c2f1", 6000, 7950, 964), ("c2f2", 7950, 10050, 964), ("c2f3", 10050, 12000, 964),
                  ("c3f1", 12000, 13950, 990), ("c3f2", 13950, 16050, 990), ("c3f3", 16050, 18000, 990),
                  ("c4f1", 18000, 19138, 948), ("c4f2", 19138, 20363, 948), ("c4f3", 20363, 21502, 948)]
        box = lambda x, y, w, h: m.Box(x, y, w, h, "product")
        SEEN = {
         "c1": (800, ("people", "establishing", "object"), "Nhóm học sinh mặc đồng phục đứng quanh bàn dài quan sát mô hình phản ứng dây chuyền gồm xe đồ chơi màu vàng, chai, quả bóng đỏ và các thanh trượt. học sinh, bàn dài, xe đồ chơi màu vàng, quả bóng đỏ, chai, giấy báo, máy tính xách tay, dây đứng quan sát mô hình", box(0, 1500, 10000, 8500), 450, True, "may-day-chuyen-hoc-sinh"),
         "c2f1": (750, ("people", "object", "reaction"), "Một nhóm thiếu niên đứng sau bàn có hàng khối gỗ xếp kiểu domino, đường dốc bằng gỗ và vài vật nhỏ nhiều màu; hai bạn nữ đang cười. khối gỗ domino, đường dốc gỗ, bàn, vật nhỏ nhiều màu, người đứng xem, cười", None, 0, True, "mo-hinh-day-chuyen-ban-go"),
         "c2f2": (375, ("people", "object"), "Hai thiếu niên mặc áo thể thao đứng cạnh một mô hình phản ứng dây chuyền đặt trên bàn, có đường ray, các hình nhân nhỏ và dốc trượt bằng gỗ. mô hình phản ứng dây chuyền, đường ray, hình nhân nhỏ, dốc gỗ, xô đỏ, thiếu niên quan sát mô hình", box(0, 3800, 10000, 6200), 350, True, "mo-hinh-day-chuyen-ban-go"),
         "c2f3": (300, ("people", "object"), "Cảnh hơi nhòe: một cậu bé và một người mặc áo khoác trắng đứng cạnh bàn có hàng domino, lon, cốc nhựa và đồ vật nhỏ; phía sau có thùng nhựa xanh, vỏ máy tính và bảng trắng. domino, lon, cốc nhựa, thùng nhựa xanh, vỏ máy tính, bảng trắng, người đứng xem", None, 0, True, "mo-hinh-day-chuyen-ban-go"),
         "c3f1": (780, ("establishing", "object", "people"), "Bàn trắng bày cỗ máy phản ứng dây chuyền: hàng quân domino, lon, chai, ly nhựa, tượng nhỏ, khung gỗ; học sinh đứng phía sau cạnh bảng trắng. quân domino, lon, chai, ly nhựa, khung gỗ, chậu nhựa xanh, đầu đĩa, tượng nhỏ, học sinh học sinh đứng quan sát", None, 0, True, "may-tu-che-lop-hoc"),
         "c3f2": (275, ("people", "object"), "Học sinh áo trắng đứng sau bàn có cỗ máy phản ứng dây chuyền tự chế bằng ống giấy, khung gỗ và đồ vật nhỏ; hình bị nhòe. học sinh, bàn, ống giấy, khung gỗ, đồ vật nhỏ đứng quan sát", box(0, 1100, 10000, 8900), 250, True, "may-tu-che-lop-hoc"),
         "c3f3": (350, ("object",), "Cận cảnh bàn với máng trượt trắng, xe đồ chơi nhỏ, sách xếp chồng; phía sau là khung gỗ, hàng domino và tượng nhỏ trên bàn khác. máng trượt, xe đồ chơi, sách, khung gỗ, quân domino, tượng nhỏ", None, 0, True, "may-tu-che-lop-hoc"),
         "c4f1": (310, ("establishing", "object", "people"), "Góc nhìn dọc bàn dài có máng trượt và sách xếp chồng; một học sinh áo kẻ sọc đứng phía sau; hình bị nhòe do chuyển động máy quay. máng trượt, sách, bàn, học sinh máy quay lia", None, 0, False, None),
         "c4f2": (360, ("people", "establishing", "object"), "Phòng học hoặc xưởng: hai thiếu niên mặc áo trắng đứng cạnh bàn dài có dựng đường ống nhựa trắng, xe đồ chơi, chai nước, giấy báo và các vật dụng của một cỗ máy phản ứng dây chuyền tự chế. thiếu niên, bàn dài, ống nhựa trắng, xe đồ chơi, giấy báo, chai nước, máy tính xách tay, kệ treo tường đứng quan sát cỗ máy", None, 300, False, None),
         "c4f3": (300, ("people", "establishing"), "Hai học sinh đứng cạnh các bàn bày cỗ máy dây chuyền trong phòng tường vàng; hình nhòe do chuyển động. học sinh, bàn, máng trượt, dụng cụ trên bàn học sinh đứng cạnh bàn", None, 0, False, None),
        }
        found = scene.Analysis(21_502, tuple(b for _, _, b, _ in bounds[:-1]), tuple(scene.Shot(a, b, 50) for _, a, b, _ in bounds),
                               (), True, "r" * 64)
        source = planner.Footage("i1-s1", Path("m2-lop-hoc-2995.webm"), found, 640, 480, (29483, 986), rights="licensed")
        pool = [shots.Candidate(f"i1-s1{n}", "i1-s1", a, b, 0, 110, shots.Quality(120, 50, q), k * 2**20 + 1, f"k{k}.jpg")
                for k, (n, a, b, q) in enumerate(bounds)]
        evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
        for n, (confidence, labels, text, product, hook, visible, same) in SEEN.items():
            evidence[f"i1-s1{n}"].add(ev.Item("vision", confidence, text, labels, "AI"))
            evidence[f"i1-s1{n}"].product_box, evidence[f"i1-s1{n}"].hook = product, hook
            evidence[f"i1-s1{n}"].product_visible, evidence[f"i1-s1{n}"].same_product = visible, same
        spoken = [("Ở vị trí thứ năm là cỗ máy của một nhóm học sinh dựng ngay trên những dãy bàn trong lớp.", 4240),
                  ("Hàng domino gỗ đổ nối tiếp nhau dọc mặt bàn trong khi cả lớp đứng xem hồi hộp.", 4000)]
        for edits in (None, align.Edits({}, {"i1-s1c1"}, set())):
            made = planner.plan_part("i1", "item", spoken, 9490, m.Options("balanced", "entertainment"), lead_ms=300, gap_ms=250,
                                     footage=[source], candidates={"i1-s1": pool}, evidence=evidence,
                                     title="Cỗ máy domino trong lớp học", inputs={"part": "0" * 64}, edits=edits)
            segments = made.plan.segments
            shown = [(s.role, s.source.start, s.source.end) for s in segments]
            self.assertEqual([s for s in segments if s.role == "still"], [], shown)  # before: 1126 ms held (c2f1)
            narration = [s for s in segments if s.audio_mode != "source"]
            self.assertEqual(sum(s.timeline.end - s.timeline.start for s in narration), 9490, shown)
            self.assertEqual([s.source.start for s in narration], sorted(s.source.start for s in narration), shown)  # forward
            if edits:
                self.assertTrue(all(s.source.start >= 6000 for s in segments), shown)

    def test_filler_that_runs_the_sentence_out_of_footage_never_goes_before_one_that_lasts(self):  # P2-N17 (#801)
        # nine shots in one source, one 10.4 s sentence: a first fix for the rc13 held frame skipped shots ahead and
        # held frames for 6.1 s here; the filler must take the shots in turn and hold none
        from engine.dubflow.mastering import candidates as shots
        from engine.dubflow.mastering.planners import compilation as planner
        bounds = [(0, 1274), (1274, 2790), (2790, 3527), (3527, 5526), (5526, 7056), (7056, 8309), (8309, 9538),
                  (9538, 11509), (11509, 12771)]
        cues = ((50, 1224, "máng trượt trắng"), (1324, 2740, "học sinh xếp domino"), (2840, 3477, "cả lớp đứng xem"),
                (9588, 11459, "xe đồ chơi chạy xuống dốc"))
        found = scene.Analysis(12771, tuple(b for _, b in bounds[:-1]), tuple(scene.Shot(a, b, 50) for a, b in bounds), (),
                               True, "q" * 64)
        source = planner.Footage("i1-s1", Path("x.webm"), found, 640, 360, (30, 1), cues=cues)
        pool = [shots.Candidate(f"i1-s1c{k + 1}", "i1-s1", a, b, k, 50, shots.Quality(120, 50, 300), k * 2**20 + 1)
                for k, (a, b) in enumerate(bounds)]
        made = planner.plan_part("i1", "item", [("Xe đồ chơi chạy xuống dốc và học sinh xếp domino.", 10355)], 10855,
                                 m.Options("balanced", "entertainment"), lead_ms=300, gap_ms=250, footage=[source],
                                 candidates={"i1-s1": pool}, evidence=planner.evidence_for([source], {"i1-s1": pool}, {}),
                                 title="xe đồ chơi chạy xuống dốc", inputs={"part": "0" * 64})
        shown = [(s.role, s.source.start, s.source.end) for s in made.plan.segments]
        self.assertFalse([s for s in made.plan.segments if s.role == "still"], shown)
        self.assertEqual([s.source.start for s in made.plan.segments], sorted(s.source.start for s in made.plan.segments), shown)

    def test_an_excluded_shot_is_never_the_moment(self):  # P2-N17
        from engine.dubflow.mastering import align, candidates as shots
        from engine.dubflow.mastering.planners import compilation as planner
        found = scene.Analysis(30_000, (6000, 12_000, 18_000, 24_000),
                               tuple(scene.Shot(a, a + 6000, 50) for a in range(0, 30_000, 6000)), (), True, "x" * 64)
        source = planner.Footage("i1-s1", Path("meo.mp4"), found, 640, 360, (25, 1),
                                 cues=((7000, 9000, "con mèo nhảy lên bàn"), (25_000, 28_000, "trời ơi")))
        quality = shots.Quality(120, 50, 300)
        pool = [shots.Candidate(f"i1-s1c{n + 1}", "i1-s1", n * 6000, n * 6000 + 6000, n, 50, quality, n * 2**20 + 1)
                for n in range(5)]
        evidence = planner.evidence_for([source], {"i1-s1": pool}, {})
        spoken = [("Con mèo nhảy lên bàn rất nhanh.", 2500), ("Ai cũng bất ngờ.", 2500)]
        made = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                 footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                 inputs={"part": "0" * 64})
        self.assertEqual(made.plan.segments[-1].source.start // 6000, 4)  # the moment with "trời ơi" is in c5
        excluded = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                     footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                     inputs={"part": "0" * 64}, edits=align.Edits({}, {"i1-s1c5"}, set()))
        self.assertFalse(any(s.source.end > 24_000 for s in excluded.plan.segments),
                         [(s.role, s.source.start, s.source.end) for s in excluded.plan.segments])
        # a part of c5 left out while c5 is not split (the split was another part's, P2-N22): c5 is never the moment
        part = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                 footage=[source], candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                 inputs={"part": "0" * 64}, edits=align.Edits({}, {"i1-s1c5f2"}, set()))
        self.assertFalse(any(s.source.end > 24_000 for s in part.plan.segments),
                         [(s.role, s.source.start, s.source.end) for s in part.plan.segments])
        self.assertFalse([w for w in part.log["warnings"] if "đoạn bạn đã bỏ" in w])
        # one shot only: kept whole, and the user is told
        alone = planner.plan_part("i1", "item", spoken, 5600, m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300,
                                  footage=[source], candidates={"i1-s1": pool[:1]}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                  inputs={"part": "0" * 64}, edits=align.Edits({}, {"i1-s1c1f2"}, set()))
        self.assertTrue([w for w in alone.log["warnings"] if "đoạn bạn đã bỏ thuộc cảnh i1-s1c1" in w], alone.log["warnings"])
        # a pinned shot that plays past the moment: the moment is chosen again after it, never from the excluded c5
        pinned = planner.plan_part("i1", "item", [spoken[0], ("Ai cũng bất ngờ.", 5000)], 8100,
                                   m.Options("balanced", "entertainment"), lead_ms=200, gap_ms=300, footage=[source],
                                   candidates={"i1-s1": pool}, evidence=evidence, title="Con mèo nhảy lên bàn",
                                   inputs={"part": "0" * 64}, edits=align.Edits({"i1.u2": ["i1-s1c4"]}, {"i1-s1c5"}, set()))
        self.assertTrue(any(s.source.start == 18_000 for s in pinned.plan.segments))  # the pin played
        self.assertFalse(any(s.source.end > 24_000 for s in pinned.plan.segments),
                         [(s.role, s.source.start, s.source.end) for s in pinned.plan.segments])

    def test_candidates_of_a_long_source_cover_all_of_it(self):
        from engine.dubflow.mastering import candidates as shots
        length = 2_400_000  # 40 minutes of 3-second shots: 800 windows
        found = scene.Analysis(length, tuple(range(3000, length, 3000)),
                               tuple(scene.Shot(a, a + 3000, 50) for a in range(0, length, 3000)), (), True, "z" * 64)
        windows = shots.windows(found)
        self.assertEqual(len(windows), shots.MAX_CANDIDATES)
        self.assertGreater(windows[-1][0], length * 9 // 10)
        self.assertEqual(windows, sorted(windows))


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

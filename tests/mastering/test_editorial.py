"""The shared editorial layer: candidates, evidence, vision, alignment (ADR-0054)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.mastering import align
from engine.dubflow.mastering import analysis as scene
from engine.dubflow.mastering import candidates as shots
from engine.dubflow.mastering import evidence as ev
from engine.dubflow.mastering import plan as m
from engine.dubflow.mastering import policy as rules
from engine.dubflow.mastering import vision

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
PACING = rules.STYLE_PACING["natural"]


def candidate(cid: str, source: str, start: int, end: int, *, usable: bool = True, dhash: int | None = None, motion: int = 50):
    quality = shots.Quality(120, 50, 300, () if usable else ("mờ, thiếu chi tiết",))
    return shots.Candidate(cid, source, start, end, 0, motion, quality, random.getrandbits(64) if dhash is None else dhash)


def unit(uid: str, text: str, start: int, end: int, function: str = "benefit") -> align.Unit:
    return align.Unit(uid, text, start, end, function)


class QualityTests(unittest.TestCase):
    def test_black_flat_dark_and_blurred_frames_are_unusable(self):
        w, h = shots.GREY_W, shots.GREY_H
        black = bytes(w * h)
        self.assertIn("một màu", shots.measure(black)[0].problems[0])
        dark = bytes((x * 7 + y * 3) % 40 for y in range(h) for x in range(w))
        self.assertEqual(shots.measure(dark)[0].problems, ("quá tối",))
        smooth = bytes(min(255, 60 + x) for y in range(h) for x in range(w))  # a gradient: contrast, no detail
        self.assertEqual(shots.measure(smooth)[0].problems, ("mờ, thiếu chi tiết",))
        rng = random.Random(1)
        detail = bytes(rng.randrange(30, 230) for _ in range(w * h))
        quality, _ = shots.measure(detail)
        self.assertTrue(quality.usable)
        self.assertGreater(quality.score, 500)

    def test_duplicates_share_a_group(self):
        a = candidate("v1c1", "v1", 0, 1000, dhash=0xFFFF0000FFFF0000)
        b = candidate("v2c1", "v2", 0, 1000, dhash=0xFFFF0000FFFF0007)  # 3 bits apart
        c = candidate("v2c2", "v2", 1000, 2000, dhash=0x0000FFFF0000FFFF)
        groups = shots.duplicate_groups([a, b, c])
        self.assertEqual(groups["v2c1"], "v1c1")
        self.assertEqual(groups["v2c2"], "v2c2")

    def test_long_takes_are_split_and_cut_margins_kept(self):
        found = scene.Analysis(30_000, (5000,), (scene.Shot(0, 5000, 10), scene.Shot(5000, 30_000, 20)), (), True, "x")
        windows = shots.windows(found)
        self.assertEqual(windows[0][:2], (0, 5000 - shots.CUT_MARGIN_MS))
        self.assertEqual(windows[1][0], 5000 + shots.CUT_MARGIN_MS)
        self.assertEqual(windows[-1][1], 30_000)
        self.assertTrue(all(b - a <= shots.WINDOW_MS + shots.WINDOW_MS // 2 for a, b, *_ in windows[1:]))


class EvidenceTests(unittest.TestCase):
    def test_folding_terms_and_cues(self):
        self.assertEqual(ev.fold("Cận cảnh LÒNG NỒI, đẹp!"), "can canh long noi dep")
        self.assertIn("cận cảnh", ev.terms("Cận cảnh lòng nồi"))
        self.assertIn("~can canh", ev.terms("can canh long noi"))  # written without marks: loose terms

    def test_a_description_typed_without_marks_is_loose_even_beside_marked_labels(self):
        item = ev.Evidence("c1")
        item.add(ev.Item("vision", 800, "crema day min tren mat tach", ("product_result",)))
        found = item.terms()
        self.assertIn("~crema day", found)
        self.assertTrue(ev.match("lớp crema", found, ev.loose(found)) or ev.match("crema", found, ev.loose(found)))
        self.assertFalse(ev.terms("Máy pha cà phê xay hạt").isdisjoint({"máy pha"}))  # marked text stays marked

    def test_marks_tell_words_apart_unless_one_side_has_none(self):
        found = {t: 900 for t in ev.terms("Cận cảnh đồng hồ áp suất của máy pha cà phê")}
        loose = ev.loose(found)
        hits = lambda text: {t for t in ev.terms(text) if ev.match(t, found, loose)}
        self.assertEqual(hits("Chỉ cần bấm nút"), set())        # "cần" is not "cận"
        self.assertEqual(hits("Dòng sản phẩm mới"), set())      # "dòng" is not "đồng"
        self.assertIn("đồng hồ", hits("Đồng hồ áp suất cho biết"))  # "hồ" is not the function word "họ"
        self.assertIn("~may pha", hits("may pha ca phe"))       # unaccented text still finds it
        named = {t: 900 for t in ev.terms(ev.file_words("may-pha_ca-phe.mp4"))}
        self.assertIn("máy pha", {t for t in ev.terms("Máy pha cà phê") if ev.match(t, named, ev.loose(named))})
        self.assertEqual(ev.cue_labels("Cận cảnh lòng nồi"), ["product_closeup"])
        self.assertEqual(ev.file_words("IMG_2034_thao-tac_bam-nut.MP4"), "thao tac bam nut")
        self.assertIn("product_demonstration", ev.cue_labels(ev.file_words("IMG_2034_thao-tac_bam-nut.MP4")))

    def test_srt_and_spans(self):
        cues = ev.parse_srt("1\n00:00:01,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:04,000\n<i>Bắt đầu</i> thôi\n\nbad")
        self.assertEqual(cues, [(1000, 2500, "Xin chào"), (3000, 4000, "Bắt đầu thôi")])
        self.assertEqual(ev.transcript_text(cues, 2000, 3500), "Xin chào Bắt đầu thôi")
        self.assertEqual(ev.transcript_text(cues, 2500, 3000), "")

    def test_boxes_are_checked(self):
        self.assertIsNone(ev.box_from({"x": 900, "y": 0, "w": 200, "h": 100}))  # outside the frame
        self.assertIsNone(ev.box_from({"x": 0, "y": 0, "w": 10, "h": 10}))      # noise
        box = ev.box_from({"x": 100, "y": 200, "w": 300, "h": 400})
        self.assertEqual((box.x, box.y, box.w, box.h, box.label), (1000, 2000, 3000, 4000, "product"))


def product_world():
    """Three clips: an overview, a closeup and a demonstration, said by the seller's notes."""
    pool = [candidate("v1c1", "v1", 0, 6000), candidate("v2c1", "v2", 0, 6000), candidate("v3c1", "v3", 0, 6000),
            candidate("v3c2", "v3", 6000, 12_000)]
    found = ev.gather(c.candidate_id for c in pool)
    notes = {"v1": "Toàn cảnh nồi chiên màu đen trên bàn bếp", "v2": "Cận cảnh lòng nồi chống dính",
             "v3": "Thao tác bấm nút chọn chế độ và cho gà vào nồi"}
    for c in pool:
        note = notes[c.source_id]
        found[c.candidate_id].add(ev.Item("user", 1000, note, tuple(ev.cue_labels(note)), "ghi chú"))
    return pool, found


class AlignTests(unittest.TestCase):
    def test_each_sentence_gets_the_footage_its_words_describe(self):
        pool, found = product_world()
        units = [unit("u1", "Lòng nồi chống dính phủ ceramic, rửa rất nhanh.", 0, 4000, "feature"),
                 unit("u2", "Chỉ cần bấm nút chọn chế độ, cho gà vào là xong.", 4000, 8000, "demonstration"),
                 unit("u3", "Nồi chiên màu đen sang trọng hợp mọi căn bếp.", 8000, 12_000, "cta")]
        choices, decisions, mode = align.assign(units, pool, found, PACING, forward_only=False)
        self.assertEqual(mode, "content")
        by_unit = {}
        for choice in choices:
            by_unit.setdefault(choice.unit_id, set()).add(choice.candidate.source_id)
        self.assertEqual(by_unit, {"u1": {"v2"}, "u2": {"v3"}, "u3": {"v1"}})
        reasons = " ".join(r for c in choices for r in c.reasons)
        self.assertIn("khớp lời", reasons)
        self.assertTrue(all(c.confidence >= 600 for c in choices))
        self.assertTrue(all(d.alternatives for d in decisions))

    def test_the_order_of_the_inputs_does_not_decide(self):
        pool, found = product_world()
        units = [unit("u1", "Cận cảnh lòng nồi chống dính.", 0, 3000, "feature")]
        for order in (pool, list(reversed(pool))):
            choices, _, _ = align.assign(units, order, found, PACING, forward_only=False)
            self.assertEqual(choices[0].candidate.source_id, "v2")

    def test_motion_alone_does_not_win(self):
        pool, found = product_world()
        busy = candidate("v4c1", "v4", 0, 6000, motion=1000)
        found["v4c1"] = ev.Evidence("v4c1")
        choices, _, _ = align.assign([unit("u1", "Cận cảnh lòng nồi chống dính.", 0, 3000, "feature")], [*pool, busy], found,
                                     PACING, forward_only=False)
        self.assertEqual(choices[0].candidate.source_id, "v2")

    def test_signals_only_is_capped_and_flagged(self):
        pool = [candidate("v1c1", "v1", 0, 6000), candidate("v2c1", "v2", 0, 6000)]
        found = ev.gather(c.candidate_id for c in pool)
        choices, _, mode = align.assign([unit("u1", "Một câu.", 0, 5000)], pool, found, PACING, forward_only=False)
        self.assertEqual(mode, "signals_only")
        self.assertTrue(all(c.confidence <= align.SIGNALS_ONLY_CAP and c.needs_review for c in choices))
        self.assertTrue(any("chưa có bằng chứng" in r for c in choices for r in c.reasons))

    def test_unusable_footage_is_skipped_while_good_footage_exists(self):
        pool, found = product_world()
        bad = candidate("v2c9", "v2", 6000, 9000, usable=False)
        found["v2c9"] = ev.Evidence("v2c9")
        found["v2c9"].add(ev.Item("user", 1000, "Cận cảnh lòng nồi chống dính cực rõ", ("product_closeup",)))
        choices, _, _ = align.assign([unit("u1", "Cận cảnh lòng nồi chống dính.", 0, 3000, "feature")], [*pool, bad], found,
                                     PACING, forward_only=False)
        self.assertNotEqual(choices[0].candidate.candidate_id, "v2c9")

    def test_edits_pin_and_exclude(self):
        pool, found = product_world()
        units = [unit("u1", "Cận cảnh lòng nồi chống dính.", 0, 3000, "feature"), unit("u2", "Bấm nút chọn chế độ.", 3000, 6000)]
        edits = align.Edits.from_dict({"pin": {"u1": ["v1c1"]}, "exclude_sources": ["v3"]})
        choices, _, _ = align.assign(units, pool, found, PACING, forward_only=False, edits=edits)
        self.assertEqual(choices[0].candidate.candidate_id, "v1c1")
        self.assertEqual(choices[0].confidence, 1000)
        self.assertIn("chọn bởi người dùng", choices[0].reasons)
        self.assertNotIn("v3", {c.candidate.source_id for c in choices})
        with self.assertRaises(ValueError):
            align.Edits.from_dict({"pin": {"u1": "v1c1"}})

    def test_the_shot_of_what_is_said_beats_a_sharper_unrelated_one(self):
        # As in the real run: the gauge shot is a little soft, the start button shot is sharp and shares one word.
        gauge = shots.Candidate("v2c1", "v2", 0, 4000, 0, 30, shots.Quality(110, 40, 60), 1)
        button = shots.Candidate("v1c1", "v1", 0, 4000, 0, 70, shots.Quality(128, 70, 900), 2**40)
        found = ev.gather(["v1c1", "v2c1"])
        found["v2c1"].add(ev.Item("vision", 700, "dong ho ap suat tren than may, kim chi vung xanh"))  # typed without marks
        found["v1c1"].add(ev.Item("vision", 800, "Ngón tay bấm nút START trên máy pha cà phê"))
        sentence = unit("u4", "Đồng hồ áp suất cho bạn biết máy đã sẵn sàng.", 0, 3000, "feature")
        choices, decisions, mode = align.assign([sentence], [button, gauge], found, PACING, forward_only=False)
        self.assertEqual(mode, "content")
        self.assertEqual(choices[0].candidate.candidate_id, "v2c1")
        self.assertTrue(any("đồng hồ" in r for r in choices[0].reasons))
        self.assertGreaterEqual(choices[0].confidence, 500)

    def test_forward_only_keeps_each_source_in_order(self):
        pool = [candidate(f"v1c{i}", "v1", i * 3000, i * 3000 + 3000) for i in range(4)]
        found = ev.gather(c.candidate_id for c in pool)
        for c, text in zip(pool, ("mở cửa", "con mèo nhảy", "đồ vật rơi", "mọi người cười")):
            found[c.candidate_id].add(ev.Item("transcript", 900, text))
        # The script talks about the end first: forward-only still plays the source in order.
        units = [unit("u1", "mọi người cười", 0, 3000, "narration"), unit("u2", "mở cửa", 3000, 6000, "narration")]
        choices, _, _ = align.assign(units, pool, found, PACING, forward_only=True)
        starts = [c.offset_ms for c in choices]
        self.assertEqual(choices[0].candidate.candidate_id, "v1c3")
        self.assertTrue(any("dùng lại" in r for r in choices[-1].reasons) or starts == sorted(starts))

    def test_units_cover_the_timeline_and_cut_in_pauses(self):
        spoken = [("Câu một.", 1000), ("Câu hai.", 2000), ("Mua ngay hôm nay.", 1500)]
        found = align.units(spoken, 300 + 1000 + 250 + 2000 + 250 + 1500 + 700, lead_ms=300, gap_ms=250,
                            function=align.product_function)
        self.assertEqual(found[0].start_ms, 0)
        self.assertEqual(found[1].start_ms, 300 + 1000 + 125)
        self.assertEqual(found[-1].end_ms, 6000)
        self.assertEqual([u.function for u in found], ["hook", "benefit", "cta"])
        self.assertTrue(all(a.end_ms == b.start_ms for a, b in zip(found, found[1:])))


class FakeAsk:
    def __init__(self, answers=None, error=None):
        self.calls, self.answers, self.error = [], answers, error

    def __call__(self, system, schema, parts):
        self.calls.append(parts)
        if self.error:
            raise self.error
        frames = sum(1 for p in parts if "image" in p)
        return {"frames": [self.answers(i) for i in range(1, frames + 1)]}, "model-x"


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.frames = []
        for i in range(8):
            path = self.root / f"f{i}.jpg"
            path.write_bytes(bytes([i]) * 50)
            self.frames.append((f"c{i}", path))

    def tearDown(self):
        self.folder.cleanup()

    def test_answers_are_validated_cached_and_budgeted(self):
        ask = FakeAsk(lambda n: {"frame": n, "description": "lòng nồi chống dính", "labels": ["product_closeup", "made_up"],
                                 "confidence": 900, "clarity": "clear", "product_box": {"x": 100, "y": 100, "w": 500, "h": 500},
                                 "product_group": "noi-den", "hook": 1500})
        describer = vision.Describer(ask, provider="gemini", model="m", cache=self.root / "cache", budget=7)
        seen = describer.describe(self.frames)
        self.assertEqual(len(seen), 7)
        self.assertEqual(seen["c0"].labels, ("product_closeup",))  # the made-up label is dropped
        self.assertEqual(seen["c0"].hook, 1000)                    # clamped
        self.assertTrue(describer.warnings)                         # one frame over the budget
        self.assertEqual(len(ask.calls), 2)                         # 6 + 1
        again = vision.Describer(ask, provider="gemini", model="m", cache=self.root / "cache", budget=7)
        self.assertEqual(len(again.describe(self.frames[:7])), 7)
        self.assertEqual(len(ask.calls), 2)                         # all from the cache
        other = vision.Describer(ask, provider="gemini", model="other", cache=self.root / "cache", budget=7)
        other.describe(self.frames[:1])
        self.assertEqual(len(ask.calls), 3)                         # another model asks again

    def test_failures_leave_no_evidence_and_stop_on_a_bad_key(self):
        class KeyError_(Exception):
            code, message = "KEY", "Khóa không hợp lệ"
        ask = FakeAsk(error=KeyError_())
        describer = vision.Describer(ask, provider="gemini", model="m", cache=self.root / "cache")
        self.assertEqual(describer.describe(self.frames), {})
        self.assertEqual(len(ask.calls), 1)
        self.assertIn("Khóa không hợp lệ", describer.warnings[0])
        junk = vision.Describer(FakeAsk(lambda n: {"frame": 99, "description": "x"}), provider="p", model="m", cache=self.root / "c2")
        self.assertEqual(junk.describe(self.frames[:2]), {})

    def test_apply_adds_vision_evidence_with_its_origin(self):
        found = ev.gather(["c0"])
        seen = vision.validate({"description": "tay bấm nút", "labels": ["product_demonstration"], "confidence": 800,
                                "clarity": "blurry", "product_box": None}, ev.LABELS)
        vision.apply({"c0": seen}, found, "AI gemini/m")
        item = found["c0"].items[0]
        self.assertEqual((item.origin, item.confidence, item.detail), ("vision", 400, "AI gemini/m"))  # blurry: halved
        self.assertTrue(found["c0"].product_visible)


class ContractTests(unittest.TestCase):
    def test_the_log_parts_have_the_contract_keys(self):
        defs = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "mastering" / "editorial-v1.json")
                          .read_text(encoding="utf-8"))["$defs"]
        pool, found = product_world()
        _, decisions, _ = align.assign([unit("u1", "Cận cảnh lòng nồi.", 0, 3000)], pool, found, PACING, forward_only=False)
        self.assertEqual(set(defs["unit"]["required"]), set(decisions[0].to_dict()))
        written = {**pool[0].to_dict(), "group": "g", "evidence": found["v1c1"].to_dict(), "summary": ""}
        self.assertEqual(set(defs["candidate"]["required"]), set(written))
        self.assertEqual(set(defs["candidate"]["properties"]["evidence"]["required"]), set(found["v1c1"].to_dict()))
        self.assertEqual(set(defs["edits"]["required"]), set(align.Edits().to_dict()))


@unittest.skipUnless(FFMPEG, "needs FFmpeg")
class RealFramesTest(unittest.TestCase):
    def test_extract_measures_real_frames(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            clip = root / "a.mp4"
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3", "-f", "lavfi",
                            "-i", "color=c=black:s=640x360:r=25:d=2", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                            "-map", "[v]", "-c:v", "mpeg4", "-q:v", "4", str(clip)], check=True)

            def run(argv, cwd, cancelled):
                subprocess.run(argv, cwd=cwd, check=True, capture_output=True)
            found = scene.Analysis(5000, (3000,), (scene.Shot(0, 3000, 300), scene.Shot(3000, 5000, 0)), (), False, "x")
            made = shots.extract(clip, "v1", found, root / "cand", ffmpeg=Path(FFMPEG), run=run)
            self.assertEqual(len(made), 2)
            self.assertTrue(made[0].quality.usable, made[0].quality)
            self.assertFalse(made[1].quality.usable)       # the black shot
            self.assertTrue((root / "cand" / made[0].keyframe).is_file())
            again = shots.extract(clip, "v7", found, root / "cand", ffmpeg=Path("missing"), run=run)  # cached, renamed
            self.assertEqual([c.candidate_id for c in again], ["v7c1", "v7c2"])


if __name__ == "__main__":
    unittest.main()

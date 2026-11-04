from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import context, script, units
from engine.dubflow.docu import __main__ as cli
from engine.dubflow.docu.project import DocuError, load_spec, script_path, spec_path, write_json

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("DUBFLOW_TEST_FFPROBE") or shutil.which("ffprobe")
DAM = "大坝浇筑了800万立方米混凝土，总投资340亿元，坝高289米，3000名工人，2021年建成"


def quantity(text: str) -> units.Quantity:
    found = units.extract(text)
    assert len(found) == 1, found
    return found[0]


class UnitTests(unittest.TestCase):
    def test_chinese_numerals(self):
        self.assertEqual(units.chinese_number("一亿三千万"), 130_000_000)
        self.assertEqual(units.chinese_number("两万亿"), 2e12)
        self.assertEqual(units.chinese_number("五万三千"), 53_000)
        self.assertEqual(units.chinese_number("十五"), 15)
        self.assertIsNone(units.chinese_number("一些"))

    def test_source_quantities(self):
        cases = {
            "浇筑800万立方米": ("volume", 8e6), "坝高289米": ("length", 289), "总投资340亿元": ("money", 34e9 * units.USD_PER_CNY),
            "装机容量1600万千瓦": ("power", 1.6e10), "时速350公里": ("speed", 350 / 3.6), "零下20摄氏度": ("temperature", -20),
            "重达一亿三千万吨": ("mass", 1.3e11), "百分之三十": ("percent", 30), "3000名工人": ("count", 3000),
            "2021年建成": ("year", 2021), "占地1.2万亩": ("area", 12_000 * 10_000 / 15), "年发电624亿度电": ("energy", 6.24e13),
            "1,200 feet": ("length", 1200 * 0.3048), "$34B": ("money", 34e9), "2.5 million cubic yards": ("volume", 2.5e6 * 0.764554857984),
            "875,000 pounds": ("mass", 875_000 * 0.45359237), "a 10,000-ton press": ("mass", 10_000 * 907.18474),
            "90°F": ("temperature", (90 - 32) * 5 / 9), "15%": ("percent", 15), "12 MW": ("power", 12e6),
        }
        for text, (dimension, value) in cases.items():
            with self.subTest(text):
                found = quantity(text)
                self.assertEqual(found.dimension, dimension)
                self.assertAlmostEqual(found.value, value, delta=abs(value) * 1e-9 + 1e-9)

    def test_traditional_characters_and_chinese_decimals(self):
        # Speech recognition wrote these in Traditional for 3 of 5 sources in the 2026-11-04 pig-tower test.
        cases = {
            "建築面積達到了40萬平方米": ("area", 400_000), "清理1200噸的豬糞": ("mass", 1_200_000),
            "佔地60畝": ("area", 60 * 10_000 / 15), "總投資約40億元": ("money", 40e8 * units.USD_PER_CNY),
            "可以養120萬頭生豬": ("count", 1_200_000), "載重40噸": ("mass", 40_000),
            "也只有百分之七点八八": ("percent", 7.88), "百分之七點八八": ("percent", 7.88),
        }
        for text, (dimension, value) in cases.items():
            with self.subTest(text):
                found = quantity(text)
                self.assertEqual(found.dimension, dimension)
                self.assertAlmostEqual(found.value, value, delta=abs(value) * 1e-9 + 1e-9)
        self.assertEqual(units.chinese_number("七点八八"), 7.88)
        self.assertIsNone(units.chinese_number("一点"))
        self.assertEqual(units.extract("一点点"), [])

    def test_rates_and_small_amounts_are_said_sensibly(self):
        # "1万吨 a day of wastewater" was compared to 25 Boeing 747s; 3.2 g came out as 0.007 pounds.
        daily = quantity("日处理能力达1万吨的污水处理中心")
        self.assertTrue(daily.rate)
        self.assertEqual(units.comparisons(daily), [])
        self.assertEqual(units.describe(daily)["imperial"], "11,020 tons")
        self.assertTrue(quantity("養豬大樓每天都要清理1200噸").rate)
        self.assertFalse(quantity("载重40吨的升降机").rate)
        self.assertTrue(units.comparisons(quantity("载重40吨的升降机")))
        self.assertEqual(units.imperial(quantity("3.2克")), "0.113 ounces")
        self.assertEqual(units.imperial(quantity("5毫米")), "0.197 inches")
        self.assertEqual(units.imperial(quantity("2公斤")), "4.409 pounds")

    def test_a_rate_said_on_the_line_before_still_counts(self):
        # 2026-11-04: "日处理能力" ended one recognised line, "达1万吨" began the next: compared to 25 Boeing 747s.
        found = units.extract_after("经过加压输送的日处理能力", "搭1万吨的污水处理中心")
        self.assertEqual([(q.text, q.rate, q.start) for q in found], [("1万吨", True, 1)])
        self.assertEqual(units.comparisons(found[0]), [])
        self.assertFalse(units.extract_after("", "搭1万吨的污水处理中心")[0].rate)
        self.assertEqual([q.text for q in units.extract_after("总投资40", "亿元")], [], "a number begun on the line before is not counted twice")

    def test_ordinary_words_and_benchmark_names_are_not_numbers(self):
        self.assertEqual(units.extract("一些人万一不来，一起去"), [])
        self.assertEqual(units.extract("the 21st century"), [])
        self.assertEqual(units.extract("A Boeing 747 lands"), [])
        found = quantity("3,200 Empire State Buildings")
        self.assertEqual((found.dimension, found.value, found.benchmark), ("count", 3200, "empire_state"))
        found = quantity("3.1 times the height of the Statue of Liberty")
        self.assertEqual((found.dimension, found.benchmark), ("ratio", "statue_of_liberty"))

    def test_conversions_and_comparisons_are_computed(self):
        volume = quantity("800万立方米")
        self.assertEqual(units.imperial(volume), "282.5 million cubic feet")
        self.assertIn("about 7.6 times the volume of the Empire State Building", units.comparisons(volume))
        self.assertEqual(units.imperial(quantity("340亿元")), "$4.76 billion")
        self.assertEqual(units.imperial(quantity("289米")), "948.2 feet")
        self.assertEqual(units.imperial(quantity("12公里")), "7.456 miles")
        self.assertEqual(units.imperial(quantity("时速350公里")), "217.5 mph")
        self.assertEqual(units.imperial(quantity("零下20摄氏度")), "-4°F")
        described = units.describe(volume)
        self.assertEqual(set(described), {"text", "dimension", "si", "imperial", "comparisons"})

    def test_narration_numbers_are_checked_not_dropped(self):
        sources = units.extract(DAM)
        good = ("Engineers poured 280 million cubic feet of concrete, about 7.6 Empire State Buildings. "
                "It cost $4.8 billion, or 34 billion yuan. The dam stands 948 feet tall, "
                "3.1 times the height of the Statue of Liberty. A Boeing 747 weighs 875,000 pounds. "
                "It opened in 2021 after 3,000 workers spent four years on it.")
        self.assertEqual(units.unverified(good, sources), [])
        bad = "Equivalent to 3,200 Empire State Buildings. It cost $34B and has 12 turbines."
        self.assertEqual(units.unverified(bad, sources), ["3,200 Empire State Buildings", "$34B", "12"])
        self.assertEqual(units.unverified(bad, sources, ["$34b", "12"]), ["3,200 Empire State Buildings"])
        self.assertEqual(units.metric_units("It is 289 meters tall and weighs 500 tonnes, 1,000 MW."), ["mét", "tấn"])


class ContextTests(unittest.TestCase):
    def test_srt_and_transcript_parsing(self):
        srt = "1\n00:00:01,000 --> 00:00:03,500\n<i>坝高289米</i>\n\n2\n00:00:04,000 --> 00:00:04,000\n空\n\n" \
              "3\r\n00:00:05.2 --> 00:00:07,000\r\n{\\an8}第二句\r\n第二行\r\n"
        self.assertEqual(context.parse_srt(srt), [(1000, 3500, "坝高289米"), (5200, 7000, "第二句 第二行")])
        transcript = {"cues": [{"start_ms": 0, "end_ms": 2980, "source_text": " 一句 "}, {"start_ms": 5, "end_ms": 5, "source_text": "x"},
                               {"start_ms": 10, "end_ms": 20, "source_text": ""}, "bad"]}
        self.assertEqual(context.parse_transcript(transcript), [(0, 2980, "一句")])
        with self.assertRaises(DocuError):
            context.parse_transcript({"cues": "x"})
        self.assertEqual(context._decode("坝高".encode("gb18030")), "坝高")

    def test_build_writes_contexts(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            summary = context.build_all(run, load_spec(spec_path(run)), lambda path: 20_000 if path.name == "a.mp4" else 9_000)
            self.assertEqual(summary, [{"id": "src_01", "duration": 20.0, "segments": 2}, {"id": "src_02", "duration": 9.0, "segments": 1}])
            first = json.loads((run / ".dubflow-work/sources/src_01/source_context.json").read_text(encoding="utf-8"))
            self.assertEqual(first["segments"][0]["seg_id"], "src_01_seg_001")
            self.assertEqual(first["segments"][0]["extracted_keywords"], ["800万立方米", "340亿元", "289米", "3000名", "2021年"])
            self.assertEqual(first["segments"][0]["quantities"][0]["imperial"], "282.5 million cubic feet")
            second = json.loads((run / ".dubflow-work/sources/src_02/source_context.json").read_text(encoding="utf-8"))
            # A line past the end of the video is dropped, a line crossing it is clamped.
            self.assertEqual([(s["start"], s["end"]) for s in second["segments"]], [(8.0, 9.0)])
            with self.assertRaises(DocuError):
                context.build_all(run, load_spec(spec_path(run)), lambda path: 0)

    def test_spec_validation(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            good = json.loads(spec_path(run).read_text(encoding="utf-8"))
            for change in ({"target_minutes": 2}, {"target_minutes": 11.0}, {"cinematic_framing_preset": "mirror"},
                           {"burn_end_credits": 0}, {"sources": good["sources"][:1]}, {"title": ""}, {"extra": 1},
                           {"schema_version": True}):
                bad = dict(good, **change)
                write_json(spec_path(run), bad)
                with self.subTest(change), self.assertRaises(DocuError):
                    load_spec(spec_path(run))
            for field, value in (("id", "src_09"), ("transcript", "relative.srt"), ("transcript", str(run / "x.txt")),
                                 ("origin_url", "javascript:x"), ("credit", "text")):
                bad = json.loads(json.dumps(good))
                bad["sources"][0][field] = value
                write_json(spec_path(run), bad)
                with self.subTest(field), self.assertRaises(DocuError):
                    load_spec(spec_path(run))


def make_run(base: Path) -> Path:
    run = base / "DubFlow Output" / "Phim tai lieu - Đập" / "run-000000000001"
    (run / ".dubflow-work").mkdir(parents=True)
    (base / "a.mp4").write_bytes(b"video")
    (base / "b.mp4").write_bytes(b"video")
    (base / "a.json").write_text(json.dumps({"schema_version": 4, "cues": [
        {"start_ms": 0, "end_ms": 4200, "source_text": DAM}, {"start_ms": 5000, "end_ms": 9000, "source_text": "机器开始工作"}]},
        ensure_ascii=False), encoding="utf-8")
    (base / "b.srt").write_text("1\n00:00:08,000 --> 00:00:12,000\n水轮机\n\n2\n00:00:10,000 --> 00:00:11,000\n过了\n", encoding="utf-8")
    write_json(spec_path(run), {"schema_version": 1, "title": "Đập Bạch Hạc Than", "topic": "", "target_minutes": 11,
                                "cinematic_framing_preset": "letterbox_235", "burn_end_credits": False, "sources": [
        {"id": "src_01", "title": "Nguồn A", "video": str(base / "a.mp4"), "transcript": str(base / "a.json"),
         "origin_url": "https://www.bilibili.com/video/BV1", "credit": {"schema_version": 1, "creator": "Kênh A", "platform": "Bilibili",
                                                                 "url": "https://www.bilibili.com/video/BV1", "rights": "unknown", "note": ""}},
        {"id": "src_02", "title": "Nguồn B", "video": str(base / "b.mp4"), "transcript": str(base / "b.srt"),
         "origin_url": None, "credit": None}]})
    return run


def draft() -> dict:
    return {"project_title": "How a Dam Defies Physics", "target_Coordinates_3D": {"lat": 27.2241, "lng": 102.9038, "location_name": "Baihetan Dam"},
            "chapters": [
                {"chapter_type": "PROBLEM", "recommended_bgm_mood": "nope", "scenes": [
                    {"narration_text": "The canyon walls were unstable.", "matched_source_refs": [{"source_id": "src_02", "start": 8.2, "end": 30}],
                     "visual_search_tags": ["canyon"], "on_screen_graphic": None, "ambient_sfx_tag": "wind_high_altitude"}]},
                {"chapter_type": "HOOK", "recommended_bgm_mood": "epic_build", "scenes": [
                    {"narration_text": "Engineers poured 280 million cubic feet of concrete.",
                     "matched_source_refs": [{"source_id": "src_01", "start": 0, "end": 4.2}, {"source_id": "src_09", "start": 0, "end": 3},
                                             {"source_id": "src_01", "start": 3, "end": 3.2}],
                     "visual_search_tags": ["aerial dam view"],
                     "on_screen_graphic": {"type": "METRIC_CALLOUT", "title": "TOTAL CONCRETE", "value": "280 Million Cubic Feet",
                                           "subtext": "Equivalent to 3,200 Empire State Buildings"},
                     "ambient_sfx_tag": "laser_guns"},
                    {"narration_text": "  ", "matched_source_refs": []}]},
                {"chapter_type": "EPILOGUE", "scenes": []},
                {"chapter_type": "ENGINEERING_SOLUTION", "recommended_bgm_mood": "curious_pulse", "scenes": [
                    {"narration_text": "Each of the 16 turbines spins at 111 rpm in a hall 438 meters long.", "matched_source_refs": [],
                     "acknowledged_numbers": ["16"]}]},
            ]}


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.run = make_run(Path(self.temp.name))
        self.spec = load_spec(spec_path(self.run))
        context.build_all(self.run, self.spec, lambda path: 20_000 if path.name == "a.mp4" else 9_000)

    def tearDown(self):
        self.temp.cleanup()

    def test_repairs_and_flags_without_dropping_scenes(self):
        result = script.check(draft(), self.spec, script.load_contexts(self.run, self.spec))
        self.assertEqual([c["chapter_type"] for c in result["chapters"]], ["HOOK", "PROBLEM", "ENGINEERING_SOLUTION"])
        self.assertEqual(result["review_notes"], ["Đã bỏ 1 chương không rõ loại", "Thiếu chương “Đánh giá”",
                                                  "Đã bỏ 2 đoạn nguồn không hợp lệ hoặc ngoài thời lượng video"])
        self.assertEqual(result["chapters"][1]["recommended_bgm_mood"], "dark_suspense")
        hook, problem, solution = (c["scenes"][0] for c in result["chapters"])
        self.assertEqual([s["scene_id"] for s in script.scenes(result)], ["sc_001", "sc_002", "sc_003"])
        self.assertEqual(hook["matched_source_refs"], [{"source_id": "src_01", "start": 0.0, "end": 4.2}])
        self.assertIsNone(hook["ambient_sfx_tag"])
        self.assertTrue(hook["needs_review"])
        self.assertEqual(hook["review_numbers"], ["3,200 Empire State Buildings"])
        self.assertEqual(problem["matched_source_refs"], [{"source_id": "src_02", "start": 8.2, "end": 9.0}])
        self.assertFalse(problem["needs_review"])
        self.assertEqual(solution["review_numbers"], ["111", "438 meters"])
        self.assertIn("Chưa có đoạn video nguồn nào khớp với cảnh này", solution["review_notes"])
        self.assertTrue(any("hệ mét" in note for note in solution["review_notes"]))
        self.assertEqual(result["target_Coordinates_3D"]["location_name"], "Baihetan Dam")
        self.assertEqual(result["script_sha256"], script.script_sha256(result))

    def test_non_english_narration_is_flagged_not_dropped(self):
        value = draft()
        value["chapters"][0]["scenes"][0]["narration_text"] = "Những bức tường hẻm núi không ổn định."
        result = script.check(value, self.spec, script.load_contexts(self.run, self.spec))
        scene = result["chapters"][1]["scenes"][0]
        self.assertTrue(scene["needs_review"])
        self.assertIn("Lời đọc không phải tiếng Anh; hãy viết lại cảnh này bằng tiếng Anh", scene["review_notes"])

    def test_acknowledging_clears_the_flag(self):
        value = draft()
        value["chapters"][1]["scenes"][0]["acknowledged_numbers"] = ["3,200 Empire State Buildings"]
        result = script.check(value, self.spec, script.load_contexts(self.run, self.spec))
        self.assertFalse(result["chapters"][0]["scenes"][0]["needs_review"])

    def test_structural_errors(self):
        contexts = script.load_contexts(self.run, self.spec)
        for bad in (None, [], {"chapters": "x"}, {"chapters": [{"chapter_type": "HOOK", "scenes": [{"narration_text": ""}]}]}):
            with self.subTest(bad), self.assertRaises(DocuError):
                script.check(bad, self.spec, contexts)

    def test_cli_check_publishes_the_script(self):
        path = self.run / ".dubflow-work" / "script-draft.json"
        write_json(path, draft())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["check", "--run-dir", str(self.run), "--script", str(path)]), 0)
        printed = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(printed, json.loads(script_path(self.run).read_text(encoding="utf-8")))
        write_json(path, {"chapters": []})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["check", "--run-dir", str(self.run), "--script", str(path)]), 1)
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])["state"], "FAILED")

    @unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
    def test_cli_context_with_real_ffprobe(self):
        for name, seconds in (("a.mp4", 20), ("b.mp4", 9)):
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x90:rate=10:duration={seconds}",
                            "-pix_fmt", "yuv420p", str(Path(self.temp.name) / name)], check=True, capture_output=True)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["context", "--spec", str(spec_path(self.run)), "--ffprobe", FFPROBE]), 0)
        result = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual([round(s["duration"]) for s in result["sources"]], [20, 9])


if __name__ == "__main__":
    unittest.main()


class ReplaceTests(unittest.TestCase):
    def test_a_reader_holding_the_file_is_waited_out(self):
        # 2026-11-04: the app polled status.json while following an MCP render; os.replace failed with WinError 5.
        from unittest.mock import patch
        from engine.dubflow.docu import project
        with TemporaryDirectory() as temp:
            target = Path(temp) / "status.json"
            calls = []
            real = project.os.replace

            def busy(source, destination):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError(5, "Access is denied")
                real(source, destination)
            with patch.object(project.os, "replace", busy), patch.object(project.time, "sleep", lambda seconds: None):
                project.write_json(target, {"state": "RUNNING"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"state": "RUNNING"})
            self.assertEqual(len(calls), 3)
            with patch.object(project.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError(5, "x"))), \
                    patch.object(project.time, "sleep", lambda seconds: None), self.assertRaises(PermissionError):
                project.write_json(target, {"state": "DONE"})

"""MCP tools for what DubFlow makes from dubbed videos (#1000): the app's specs, its lease and its run folders."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import mcp_tools
from engine.dubflow.docu.project import DocuError
from engine.dubflow.mcp import leased, outputs

VOICES = {"default_voice_id": "vi-a", "voices": [{"voice_id": "vi-a", "name": "A", "approved": True},
                                                 {"voice_id": "vi-b", "name": "B", "approved": True}]}
VERTICAL = {"width": 1080, "height": 1920, "duration_s": 60.0, "quality": "1080p"}


class OutputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.root = root
        self.app = root / "versions" / "9.9.9" / "app"
        (self.app / "models" / "manifests").mkdir(parents=True)
        (self.app / "models" / "manifests" / "production-vieneu-v1.json").write_text(json.dumps(VOICES), encoding="utf-8")
        self.install = mcp_tools.Install(self.app, root / "ffmpeg.exe", root / "ffprobe.exe", root / "data" / "models")
        self.video = self.dubbed("Đập Tam Hiệp", "0123456789ab")
        self.spawned = []

    def tearDown(self):
        self.temp.cleanup()

    def dubbed(self, title, run_id):
        folder = self.root / "DubFlow Output" / f"{title} - vi" / f"run-{run_id}"
        (folder / ".dubflow-work").mkdir(parents=True)
        (folder / "final_vi.mp4").write_bytes(b"\x00")
        cues = [{"cue_id": "c1", "start_ms": 0, "end_ms": 4000, "source_text": "大坝", "translated_text": "Con đập khổng lồ"},
                {"cue_id": "c2", "start_ms": 4000, "end_ms": 9000, "source_text": "发电", "translated_text": "phát điện"}]
        (folder / ".dubflow-work" / "translation.json").write_text(json.dumps({"schema_version": 1, "cues": cues}), encoding="utf-8")
        (folder / "translation_overrides.json").write_text(json.dumps({"cues": {"c2": "Phát điện cho cả nước"}}), encoding="utf-8")
        (folder / "job_manifest.json").write_text(json.dumps({"audio": {"voice_id": "vi-b"}}), encoding="utf-8")
        return folder / "final_vi.mp4"

    def spawn(self, argv, work, cwd):
        self.spawned.append((argv, work, cwd))

    def work(self, kind):
        return self.video.parent / ".dubflow-work" / kind

    def test_only_a_finished_dubbed_video(self):
        other = self.root / "elsewhere" / "final_vi.mp4"
        other.parent.mkdir()
        other.write_bytes(b"x")
        bare = self.root / "DubFlow Output" / "x - vi" / "run-aaaaaaaaaaaa"
        bare.mkdir(parents=True)
        (bare / "final_vi.mp4").write_bytes(b"x")
        for value in ("final_vi.mp4", str(other), str(bare / "final_vi.mp4"), str(self.video.parent / "captions_vi.srt")):
            with self.subTest(value=value), self.assertRaises(DocuError):
                outputs.finished_video(value)
        self.assertEqual(outputs.finished_video(str(self.video)), self.video)

    def test_details_with_the_reviewed_subtitles(self):
        got = outputs.dubbed_video({"video": str(self.video)}, self.install, probe=lambda video: VERTICAL)
        self.assertEqual((got["title"], got["vertical"], got["voice_id"]), ("Đập Tam Hiệp", True, "vi-b"))
        self.assertEqual([line["text"] for line in got["subtitles"]], ["Con đập khổng lồ", "Phát điện cho cả nước"])
        self.assertEqual(got["made"], {"shorts": None, "reframe": None, "package": None})
        self.assertNotIn("subtitles", outputs.dubbed_video({"video": str(self.video), "subtitles": False}, self.install,
                                                           probe=lambda video: VERTICAL))

    def test_shorts_like_the_panel_under_the_lease(self):
        (self.work("shorts")).mkdir(parents=True)
        (self.work("shorts") / "cancel").write_bytes(b"")
        made = outputs.make_shorts({"video": str(self.video), "items": [{"start_ms": 0, "end_ms": 9000, "title": " Con  đập "}]},
                                   self.install, spawn=self.spawn)
        self.assertEqual(made["state"], "RUNNING")
        work = self.work("shorts")
        spec = json.loads((work / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(spec, {"schema_version": 1, "video": str(self.video), "items": [{"start_ms": 0, "end_ms": 9000, "title": "Con đập"}],
                                "layout": "auto"})
        status = json.loads((work / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["done"], status["total"], status["outputs"]), ("RUNNING", 0, 1, []))
        self.assertFalse((work / "cancel").exists())
        self.assertFalse((work / "spec.mcp-check.json").exists())
        [(argv, spawned_work, cwd)] = self.spawned
        self.assertEqual(argv[1:8], ["-B", "-m", "engine.dubflow.mcp.leased", "--kind", "shorts", "--work", str(work)])
        engine = argv[argv.index("--") + 1:]
        self.assertEqual(engine[1:6], ["-B", "-m", "engine.dubflow.shorts", "--spec", str(work / "spec.json")])
        self.assertEqual(engine[engine.index("--output-dir") + 1], str(self.video.parent))
        self.assertNotIn("--model-root", engine)
        self.assertEqual((spawned_work, cwd), (work, self.app))

    def test_the_engine_refuses_before_anything_changes(self):
        work = self.work("shorts")
        work.mkdir(parents=True)
        (work / "status.json").write_text('{"state": "COMPLETED"}', encoding="utf-8")
        for items in ([{"start_ms": 0, "end_ms": 3000, "title": "ngắn"}], [{"start_ms": 0, "end_ms": 9000, "title": "x"}] * 4,
                      [{"start_ms": 0, "end_ms": 9000, "title": " "}]):
            with self.subTest(items=items), self.assertRaises(DocuError):
                outputs.make_shorts({"video": str(self.video), "items": items}, self.install, spawn=self.spawn)
        self.assertEqual((work / "status.json").read_text(encoding="utf-8"), '{"state": "COMPLETED"}')
        self.assertFalse((work / "spec.json").exists())
        self.assertEqual(self.spawned, [])

    @unittest.skipUnless(sys.platform == "win32", "the lease is a Windows sharing lock")
    def test_a_held_lease_refuses_a_second_start(self):
        work = self.work("shorts")
        work.mkdir(parents=True)
        with leased.Lease(work):
            self.assertFalse(leased.lease_free(work))
            with self.assertRaises(DocuError) as caught:
                outputs.make_shorts({"video": str(self.video), "items": [{"start_ms": 0, "end_ms": 9000, "title": "x"}]},
                                    self.install, spawn=self.spawn)
            self.assertIn("đang được cắt Shorts", str(caught.exception))
            self.assertEqual(leased.main(["--kind", "shorts", "--work", str(work), "--", sys.executable, "-c", "pass"]), 3)
        self.assertTrue(leased.lease_free(work))

    def test_landscape_only_from_a_vertical_video_with_the_panel_defaults(self):
        with self.assertRaises(DocuError):
            outputs.make_landscape({"video": str(self.video), "mode": "cat-ngang"}, self.install,
                                   probe=lambda video: {"width": 1920, "height": 1080}, spawn=self.spawn)
        outputs.make_landscape({"video": str(self.video), "mode": "bang-thong-tin", "title": "Đập", "score": 9, "pros": ["To"],
                                "summary": "Lớn nhất"}, self.install, probe=lambda video: VERTICAL, spawn=self.spawn)
        spec = json.loads((self.work("reframe") / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual({key: spec[key] for key in ("mode", "score", "pros", "cons", "focus_start", "focus_end", "start_ms", "end_ms")},
                         {"mode": "bang-thong-tin", "score": 9, "pros": ["To"], "cons": [], "focus_start": 50, "focus_end": 50,
                          "start_ms": 0, "end_ms": None})
        self.assertEqual(json.loads((self.work("reframe") / "status.json").read_text(encoding="utf-8"))["state"], "RUNNING")

    def test_package_uses_the_videos_own_voice_and_the_models(self):
        outputs.package({"video": str(self.video), "intro": "Xin chào các bạn", "cpu_only": True}, self.install, spawn=self.spawn)
        spec = json.loads((self.work("package") / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual((spec["voice_id"], spec["intro"], spec["outro"], spec["background"], spec["music"]),
                         ("vi-b", "Xin chào các bạn", "", "keep", None))
        engine = self.spawned[0][0]
        self.assertEqual(engine[engine.index("--model-root") + 1], str(self.install.model_root))
        self.assertEqual(engine[-1], "--cpu-only")
        with self.assertRaises(DocuError):  # nothing to add
            outputs.package({"video": str(self.video)}, self.install, spawn=self.spawn)
        with self.assertRaises(DocuError):
            outputs.package({"video": str(self.video), "intro": "x", "voice_id": "vi-x"}, self.install, spawn=self.spawn)

    def test_job_state_and_cancel(self):
        self.assertEqual(outputs.video_job(str(self.video), "reframe")["state"], None)
        with self.assertRaises(DocuError):
            outputs.cancel_video_job(str(self.video), "reframe")
        work = self.work("shorts")
        work.mkdir(parents=True)
        (work / "status.json").write_text(json.dumps({"state": "RUNNING", "done": 1, "total": 2, "outputs": [
            {"path": "C:/x/short-1.mp4", "text_path": "C:/x/short-1.txt", "title": "Một"}]}), encoding="utf-8")
        job = outputs.video_job(str(self.video), "shorts")
        self.assertEqual((job["done"], job["outputs"][0]["video"]), (1, "C:/x/short-1.mp4"))
        outputs.cancel_video_job(str(self.video), "shorts")
        self.assertTrue((work / "cancel").is_file())
        (self.work("package")).mkdir(parents=True)
        (self.work("package") / "status.json").write_text('{"state": "COMPLETED", "duration_ms": 61000}', encoding="utf-8")
        (self.video.parent / "final_vi_packaged.mp4").write_bytes(b"x")
        self.assertEqual(outputs.video_job(str(self.video), "package")["output"], str(self.video.parent / "final_vi_packaged.mp4"))
        with self.assertRaises(DocuError):
            outputs.video_job(str(self.video), "seo")

    def test_the_runner_settles_like_the_app(self):
        work = self.work("shorts")
        work.mkdir(parents=True)
        (work / "status.json").write_text(json.dumps({"schema_version": 1, "state": "RUNNING", "outputs": [{"path": "a"}]}), encoding="utf-8")
        self.assertEqual(leased.main(["--kind", "shorts", "--work", str(work), "--", sys.executable, "-c", "pass"]), 0)
        status = json.loads((work / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["message"], status["outputs"]), ("FAILED", "Bộ cắt Shorts đã dừng. Hãy thử lại.", [{"path": "a"}]))
        (work / "status.json").write_text('{"state": "RUNNING"}', encoding="utf-8")
        (work / "cancel").write_bytes(b"")
        leased.settle(work, "package")
        self.assertEqual(json.loads((work / "status.json").read_text(encoding="utf-8"))["message"], "Đã hủy đóng gói.")
        (work / "status.json").write_text('{"state": "COMPLETED"}', encoding="utf-8")
        leased.settle(work, "reframe")
        self.assertEqual(json.loads((work / "status.json").read_text(encoding="utf-8")), {"state": "COMPLETED"})

    def test_post_suggestion_from_the_subtitles(self):
        got = outputs.suggest({"video": str(self.video)})
        self.assertTrue(got["titles"] and got["description"] and got["hashtags"])

    def test_compilation_like_the_page(self):
        second = self.dubbed("Nhà máy", "bbbbbbbbbbbb")
        records = []
        title = "Top công trình khổng lồ của Trung Quốc năm nay: đập, cầu, cảng"
        made = outputs.create_compilation(
            {"title": title, "layout": "bang-thong-tin", "voice_id": "vi-a", "intro": "Chào mừng các bạn",
             "items": [{"video": str(self.video), "title": "Đập", "lead": "Đầu tiên là con đập", "score": 9, "pros": ["To"]},
                       {"video": str(second), "title": "Nhà máy", "start_ms": 1000, "end_ms": 8000}]},
            self.install, record_run=lambda kind, run: records.append((kind, run)), spawn=self.spawn)
        run = Path(made["run"])
        self.assertEqual(run.parent.parent, self.root / "DubFlow Output")
        self.assertEqual(run.parent.name, "Tong hop - " + title.replace(":", " ")[:40].rstrip(" ."))
        self.assertRegex(run.name, r"^run-[0-9a-f]{12}$")
        spec = json.loads((run / ".dubflow-work" / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(spec), ["intro", "items", "layout", "music", "music_volume", "outro", "schema_version", "speed", "title",
                                        "voice_id"])
        self.assertEqual(spec["items"][1], {"video": str(second), "title": "Nhà máy", "lead": "", "score": None, "pros": [], "cons": [],
                                            "start_ms": 1000, "end_ms": 8000})
        status = json.loads((run / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["total"], status["path"]), ("RUNNING", 4, None))  # 2 parts, 1 lead, the intro
        self.assertEqual(records, [("compilation", run)])
        argv = self.spawned[0][0]
        self.assertEqual(argv[1:4], ["-B", "-m", "engine.dubflow.compilation"])
        self.assertEqual(outputs.compilation(str(run))["state"], "RUNNING")
        self.assertEqual([item["run"] for item in outputs.list_compilations([self.root / "DubFlow Output"])], [str(run)])
        for bad in ({"items": [{"video": str(self.video), "title": "x"}]}, {"voice_id": "vi-x"},
                    {"items": [{"video": str(self.video), "title": "x"}, {"video": str(self.root / "x.mp4"), "title": "y"}]}):
            with self.subTest(bad=bad), self.assertRaises(DocuError):
                outputs.create_compilation({"title": "T", "layout": "toan-man-hinh", "voice_id": "vi-a",
                                            "items": [{"video": str(self.video), "title": "a"}, {"video": str(second), "title": "b"}], **bad},
                                           self.install, record_run=lambda kind, run: None, spawn=self.spawn)
        with self.assertRaises(DocuError):
            outputs.compilation(str(self.video.parent))

    def test_dubbed_videos_from_the_queue_and_the_folders(self):
        data = self.root / "data"
        (data / "control" / "jobs").mkdir(parents=True)
        (data / "control" / "jobs" / "mcp-1.json").write_text(json.dumps({"output_path": str(self.video), "status": {"state": "COMPLETED"}}),
                                                              encoding="utf-8")
        (data / "control" / "jobs" / "job-2.json").write_text(json.dumps({"output_path": None, "status": {"state": "RUNNING"}}), encoding="utf-8")
        second = self.dubbed("Nhà máy", "bbbbbbbbbbbb")
        found = outputs.list_dubbed_videos(data, [self.root / "DubFlow Output"])
        self.assertEqual(sorted(item["video"] for item in found), sorted([str(self.video), str(second)]))
        self.assertEqual({item["title"] for item in found}, {"Đập Tam Hiệp", "Nhà máy"})


if __name__ == "__main__":
    unittest.main()

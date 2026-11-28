"""MCP Bán hàng tools (#996): the run the app's page makes, checked by the engine before it starts."""
from __future__ import annotations

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import mcp_tools
from engine.dubflow.docu.project import DocuError
from engine.dubflow.mcp import studio
from engine.dubflow.mcp.server import TOOLS, Server, serve

VOICES = {"voices": [{"voice_id": "vi-a", "name": "A", "gender": "Nam", "accent": "Bắc", "style": "tự nhiên", "description": "x", "approved": True},
                     {"voice_id": "vi-hidden", "name": "B", "approved": False}]}


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = root / "versions" / "9.9.9" / "app"
        (self.app / "models" / "manifests").mkdir(parents=True)
        (self.app / "models" / "manifests" / "production-vieneu-v1.json").write_text(json.dumps(VOICES), encoding="utf-8")
        self.install = mcp_tools.Install(self.app, root / "ffmpeg.exe", root / "ffprobe.exe", root / "data" / "models")
        self.photos = root / "photos"
        self.photos.mkdir()
        for name in ("a.png", "b.jpg"):
            (self.photos / name).write_bytes(b"\x89PNG")
        self.spawned = []
        self.records = []

    def tearDown(self):
        self.temp.cleanup()

    def make(self, **overrides):
        args = {"name": "Máy xay sinh tố", "price": "499.000đ", "scripts": ["Xay nhuyễn mọi thứ trong 30 giây. Mua ngay!"],
                "visuals": [str(self.photos / "a.png"), str(self.photos / "b.jpg")], "features": ["Lưỡi dao 6 cánh"], "voice_id": "vi-a"}
        args.update(overrides)
        return studio.create_product_video(args, self.install, record_run=lambda kind, run: self.records.append((kind, run)),
                                           base_default=Path(self.temp.name), spawn=lambda argv, work, cwd: self.spawned.append((argv, work, cwd)))

    def test_voices_are_the_approved_ones(self):
        self.assertEqual([voice["voice_id"] for voice in studio.voices(self.app)], ["vi-a"])

    def test_a_run_like_the_app_page_and_the_engine_started(self):
        made = self.make()
        run = Path(made["run"])
        self.assertEqual(run.parent.parent, self.photos / "DubFlow Output")
        self.assertEqual(run.parent.name, "Ban hang - Máy xay sinh tố")
        self.assertRegex(run.name, r"^run-[0-9a-f]{12}$")
        spec = json.loads((run / ".dubflow-work" / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual((spec["schema_version"], spec["template"], spec["voice_id"], spec["variants"]),
                         (1, "ban-tin", "vi-a", ["Xay nhuyễn mọi thứ trong 30 giây. Mua ngay!"]))
        status = json.loads((run / ".dubflow-work" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["total"]), ("RUNNING", 1))
        self.assertEqual(self.records, [("product", run)])
        [(argv, work, cwd)] = self.spawned
        self.assertEqual(argv[1:4], ["-B", "-m", "engine.dubflow.product"])
        self.assertEqual(argv[argv.index("--output-dir") + 1], str(run))
        self.assertEqual(argv[argv.index("--model-root") + 1], str(self.install.model_root))
        self.assertEqual((work, cwd), (run / ".dubflow-work", self.app))

    def test_text_only_needs_no_photos_and_output_root_is_honoured(self):
        out = Path(self.temp.name) / "out"
        out.mkdir()
        made = self.make(template="chi-chu", visuals=[], output_root=str(out))
        self.assertTrue(made["run"].startswith(str(out / "DubFlow Output")))

    def test_refusals_start_nothing(self):
        for overrides in ({"name": " "}, {"scripts": []}, {"scripts": ["a"] * 6}, {"template": "khac"}, {"visuals": []},
                          {"visuals": ["relative.png"]}, {"voice_id": "vi-hidden"}, {"music": "C:/nope.mp3"},
                          {"output_root": "relative"}):
            with self.subTest(overrides), self.assertRaises(DocuError):
                self.make(**overrides)
        self.assertEqual(self.spawned, [])

    def test_the_engine_check_fails_the_run_before_it_starts(self):
        with self.assertRaises(DocuError):
            self.make(speed=1.9)
        self.assertEqual(self.spawned, [])

    def test_status_and_list(self):
        made = self.make()
        run = Path(made["run"])
        (run / "video-1.mp4").write_bytes(b"x")
        state = studio.product_video(made["run"])
        self.assertEqual((state["state"], state["videos"]), ("RUNNING", [str(run.resolve() / "video-1.mp4")]))
        listed = studio.list_product_videos([self.photos / "DubFlow Output"])
        self.assertEqual([item["run"] for item in listed], [str(run)])
        for bad in ("relative", str(self.photos), str(run.parent)):
            with self.subTest(bad), self.assertRaises(DocuError):
                studio.product_video(bad)

    def test_the_server_lists_and_labels_them(self):
        from engine.dubflow.mcp.activity import label
        names = {tool["name"] for tool in TOOLS}
        for name in ("list_vietnamese_voices", "create_product_video", "get_product_video", "list_product_videos"):
            self.assertIn(name, names)
            self.assertNotEqual(label(name, {}), name)
        server = Server(self.install, roots=lambda: [], activity=False)
        out = io.StringIO()
        serve(server, io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "list_vietnamese_voices", "arguments": {}}}) + "\n"), out)
        result = json.loads(out.getvalue())["result"]
        self.assertFalse(result["isError"])


if __name__ == "__main__":
    unittest.main()

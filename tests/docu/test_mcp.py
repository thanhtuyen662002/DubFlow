from __future__ import annotations

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu import matcher, mcp_tools, roughcut, visual
from engine.dubflow.docu.project import DocuError, write_json, work_dir
from engine.dubflow.mcp.server import TOOLS, Server, serve

from test_docu import make_run


def _planned(run: Path) -> None:
    write_json(run / "documentary_script.json", {"project_title": "Dam", "script_sha256": "s", "chapters": [
        {"chapter_type": "HOOK", "scenes": [{"scene_id": "sc_001", "narration_text": "The dam rises.", "needs_review": False, "review_notes": []}]}]})
    write_json(run / "master_audio_timeline.json", {"duration": 8.0, "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 7.5, "slot_end": 8.0}]})
    write_json(run / "visual_plan.json", {"script_sha256": "s", "duration": 8.0,
                                          "sources": [{"id": "src_01", "duration": 20.0, "framing": {}}, {"id": "src_02", "duration": 9.0, "framing": {}}],
                                          "scenes": [{"scene_id": "sc_001", "start": 0.0, "end": 8.0, "warnings": [], "slices": [
                                              {"source_id": "src_01", "start": 1.0, "end": 5.0, "at": 0.0, "tier": 0},
                                              {"source_id": "src_02", "start": 2.0, "end": 6.0, "at": 4.0, "tier": 0}]}], "warnings": []})


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.run = make_run(Path(self.temp.name))
        _planned(self.run)
        self.install = mcp_tools.Install(Path(self.temp.name), Path("ffmpeg"), Path("ffprobe"), Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_project_boundary(self):
        self.assertEqual(mcp_tools.project(str(self.run)), self.run.resolve())
        for bad in ("", "relative/run-000000000001", str(self.run.parent), str(Path(self.temp.name))):
            with self.subTest(bad), self.assertRaises(DocuError):
                mcp_tools.project(bad)

    def test_inspect_and_list(self):
        state = mcp_tools.inspect(self.run)
        scene = state["chapters"][0]["scenes"][0]
        self.assertEqual([piece["source_id"] for piece in scene["slices"]], ["src_01", "src_02"])
        self.assertEqual(scene["end"], 8.0)
        listed = mcp_tools.list_projects([self.run.parent.parent])
        self.assertEqual([item["project"] for item in listed], [str(self.run.resolve())])
        self.assertTrue(listed[0]["plan"])

    def test_replace_keeps_the_length(self):
        result = mcp_tools.replace_clip(self.run, "sc_001", 1, "src_01", 12.0)
        self.assertEqual(result["slice"], {"source_id": "src_01", "start": 12.0, "end": 16.0, "at": 4.0, "tier": -1})
        for args in (("sc_009", 0, "src_01", 1.0), ("sc_001", 5, "src_01", 1.0), ("sc_001", 0, "src_09", 1.0), ("sc_001", 0, "src_02", 6.0)):
            with self.subTest(args), self.assertRaises(DocuError):
                mcp_tools.replace_clip(self.run, *args)

    def test_labels_are_kept_and_burned(self):
        mcp_tools.apply_label(self.run, "sc_001", "Turbine hall\n320 ft", 0.5, 0.4, 0.3)
        labels = roughcut.load_labels(self.run)
        self.assertEqual(labels[0]["id"], "l001")
        ass = roughcut.labels_ass(labels, 4.0)
        self.assertIn("{\\pos(960,432)}Turbine hall\\N320 ft", ass)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:04.00,Label,,672,672", ass)
        graph = roughcut.video_filter(1920, 1080, {}, frames=120, ken_burns=False, labels="slice-0001.ass")
        self.assertIn("subtitles=slice-0001.ass,fps=30", graph)
        for args in (("sc_009", "x", 0.5, 0.5, 0.3), ("sc_001", "", 0.5, 0.5, 0.3), ("sc_001", "x" * 200, 0.5, 0.5, 0.3),
                     ("sc_001", "x", 1.5, 0.5, 0.3), ("sc_001", "x", 0.5, 0.5, 0.01)):
            with self.subTest(args), self.assertRaises(DocuError):
                mcp_tools.apply_label(self.run, *args)
        mcp_tools.remove_label(self.run, "l001")
        self.assertEqual(roughcut.load_labels(self.run), [])
        with self.assertRaises(DocuError):
            mcp_tools.remove_label(self.run, "l001")

    def test_pacing_is_saved_then_the_plan_rebuilt(self):
        replans = []

        def replan(run):
            replans.append(visual.load_pacing(run))
            return {"state": "COMPLETED"}
        result = mcp_tools.adjust_pacing(self.run, self.install, chapter="HOOK", max_clip_duration=2.5, ken_burns_intensity=0.0, replan=replan)
        self.assertEqual(result["pacing"], {"max_slice": {"HOOK": 2.5}, "ken_burns": 0.0})
        self.assertEqual(replans[0]["max_slice"], {"HOOK": 2.5})
        everywhere = mcp_tools.adjust_pacing(self.run, self.install, chapter="all", max_clip_duration=6, ken_burns_intensity=None, replan=replan)
        self.assertEqual(set(everywhere["pacing"]["max_slice"]), {"HOOK", "PROBLEM", "ENGINEERING_SOLUTION", "CRITICAL_EVALUATION"})
        self.assertEqual(everywhere["pacing"]["ken_burns"], 0.0)
        for kwargs in ({"chapter": "X", "max_clip_duration": 3}, {"chapter": None, "max_clip_duration": 1}, {"chapter": None, "max_clip_duration": None, "ken_burns_intensity": 0.5}):
            with self.subTest(kwargs), self.assertRaises(DocuError):
                mcp_tools.adjust_pacing(self.run, self.install, **{"ken_burns_intensity": None, **kwargs}, replan=replan)
        with self.assertRaises(DocuError):
            mcp_tools.adjust_pacing(self.run, self.install, chapter=None, max_clip_duration=3, ken_burns_intensity=None,
                                    replan=lambda run: {"state": "FAILED", "message": "x"})
        # The matcher follows a faster pace.
        fast = matcher.plan([matcher.Scene("sc_001", 0, 10, (matcher.Ref("src_01", 0, 60),), 2.5)], [matcher.Source("src_01", 60, tuple(float(t) for t in range(2, 60, 2)))])
        self.assertTrue(all(piece.duration <= 2.5 + 0.41 for piece in fast[0][1]))
        self.assertEqual(len(fast[0][1]), 4)


class ProtocolTests(unittest.TestCase):
    def test_handshake_tools_and_errors_over_stdio(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            _planned(run)
            server = Server(mcp_tools.Install(Path(temp), Path("ffmpeg"), Path("ffprobe"), Path(temp)), roots=lambda: [run.parent.parent])
            lines = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "inspect_timeline_state", "arguments": {"project": "C:/nowhere"}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
                {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            ]
            out = io.StringIO()
            serve(server, io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n{broken\n"), out)
            replies = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertEqual([reply.get("id") for reply in replies], [1, 2, 3, 4, 5, 6, 7, None])
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
            self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]}, {tool["name"] for tool in TOOLS})
            self.assertEqual(len(TOOLS), 9)
            self.assertFalse(replies[2]["result"]["isError"])
            self.assertEqual(len(json.loads(replies[2]["result"]["content"][0]["text"])), 1)
            self.assertTrue(replies[3]["result"]["isError"])
            self.assertEqual(replies[4]["error"]["code"], -32602)
            self.assertEqual(replies[5]["error"]["code"], -32601)
            self.assertEqual(replies[6]["result"], {})
            self.assertEqual(replies[7]["error"]["code"], -32700)
            for tool in TOOLS:
                self.assertEqual(tool["inputSchema"]["type"], "object")


if __name__ == "__main__":
    unittest.main()

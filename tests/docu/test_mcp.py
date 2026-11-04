from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
        roots = [mcp_tools.output_root(str(self.run.parent.parent))]
        self.assertEqual(mcp_tools.project(str(self.run), roots), self.run.resolve())
        for bad in ("", "relative/run-000000000001", str(self.run.parent), str(Path(self.temp.name))):
            with self.subTest(bad), self.assertRaises(DocuError):
                mcp_tools.project(bad, roots)
        with self.assertRaises(DocuError):
            mcp_tools.project(str(self.run), [])
        for bad in ("", "relative", str(Path(self.temp.name) / "missing"), str(self.run / ".dubflow-work" / "spec.json")):
            with self.subTest(root=bad), self.assertRaises(DocuError):
                mcp_tools.output_root(bad)

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

    def test_hand_picked_slices_survive_a_new_plan(self):
        mcp_tools.replace_clip(self.run, "sc_001", 1, "src_01", 12.0)
        plan = json.loads((self.run / "visual_plan.json").read_text(encoding="utf-8"))
        # A new plan (pacing, framing, rescan) cuts the scene differently: the choice stays, at the new length.
        plan["scenes"][0]["slices"] = [{"source_id": "src_02", "start": 0.0, "end": 2.5, "at": 0.0, "tier": 0},
                                       {"source_id": "src_02", "start": 5.0, "end": 8.0, "at": 2.5, "tier": 0},
                                       {"source_id": "src_01", "start": 2.0, "end": 4.5, "at": 5.5, "tier": 0}]
        self.assertEqual(visual.apply_overrides(self.run, plan), [])
        self.assertEqual(plan["scenes"][0]["slices"][1], {"source_id": "src_01", "start": 12.0, "end": 15.0, "at": 2.5, "tier": -1})
        # Fewer slices than the remembered index: said, not silently lost.
        plan["scenes"][0]["slices"] = plan["scenes"][0]["slices"][:1]
        self.assertIn("không còn chỗ", visual.apply_overrides(self.run, plan)[0])
        # A changed script drops them, and says so.
        plan["script_sha256"] = "other"
        self.assertIn("Kịch bản đã đổi", visual.apply_overrides(self.run, plan)[0])
        self.assertFalse((work_dir(self.run) / visual.OVERRIDES).exists())

    def test_framing_changes_without_a_new_plan(self):
        plan = json.loads((self.run / "visual_plan.json").read_text(encoding="utf-8"))
        band = {"top": 0.86, "bottom": 0.93, "share": 0.9}
        plan["sources"][0]["framing"] = {"band": band, "zoom": 1.14, "shift": 0.0614, "letterbox": 0.0, "clear": False}
        plan["sources"][1]["framing"] = {"band": None, "zoom": 1.0, "shift": 0.0, "letterbox": 0.0, "clear": True, "portrait": True}
        plan["warnings"] = [visual.caption_warning("src_01", "none"), visual.portrait_warning("src_02"), "Kế hoạch khác"]
        write_json(self.run / "visual_plan.json", plan)
        slices = plan["scenes"]
        result = visual.reframe(self.run, "letterbox_235")
        self.assertEqual(result["sources"], [{"source_id": "src_01", "clear": True}, {"source_id": "src_02", "clear": True}])
        saved = json.loads((self.run / "visual_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["scenes"], slices)
        self.assertEqual(saved["sources"][0]["framing"]["band"], band)
        self.assertAlmostEqual(saved["sources"][0]["framing"]["letterbox"], 0.1217, places=3)
        self.assertEqual(saved["warnings"], [visual.portrait_warning("src_02"), "Kế hoạch khác"])
        self.assertEqual(json.loads((work_dir(self.run) / "spec.json").read_text(encoding="utf-8"))["cinematic_framing_preset"], "letterbox_235")
        with self.assertRaises(DocuError):
            visual.reframe(self.run, "wide")

    def test_a_label_can_cover_one_slice_only(self):
        label = mcp_tools.apply_label(self.run, "sc_001", "Disinfection room", 0.6, 0.8, 0.25, 1)["label"]
        self.assertEqual(label["clip_index"], 1)
        for bad in (2, -1, "1"):
            with self.subTest(bad), self.assertRaises(DocuError):
                mcp_tools.apply_label(self.run, "sc_001", "x", 0.5, 0.5, 0.3, bad)

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


OUTSIDE = "nằm ngoài thư mục DubFlow Output"
LINKED = "liên kết"


def _link_dir(link: Path, target: Path) -> None:
    """A directory junction on Windows (a reparse point, no privilege needed), a directory symlink elsewhere."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _snapshot(base: Path) -> dict[str, bytes]:
    return {str(path.relative_to(base)): path.read_bytes() if path.is_file() else b"<dir>" for path in sorted(base.rglob("*"))}


class BoundaryTests(unittest.TestCase):
    """Tools reach only runs directly under a chosen DubFlow Output root, and never through a link."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        base = Path(self.temp.name)
        self.run = make_run(base / "home")
        _planned(self.run)
        self.root = self.run.parent.parent
        self.outside = base / "elsewhere"
        self.outside_run = make_run(self.outside)
        _planned(self.outside_run)
        self.marker = self.outside / "marker.json"
        self.marker.write_bytes(b'{"keep": "these exact bytes"}\n')
        self.before = _snapshot(self.outside)
        self.server = Server(mcp_tools.Install(base, Path("ffmpeg"), Path("ffprobe"), base), roots=lambda: [self.root], activity=False)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name: str, **args) -> tuple[bool, str]:
        reply = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        result = reply["result"]
        return result["isError"], result["content"][0].get("text", "")

    def refused(self, project: Path, why: str) -> None:
        calls = {"inspect_timeline_state": {}, "apply_smart_label_overlay": {"scene_id": "sc_001", "text_en": "x", "pos_x": 0.5, "pos_y": 0.5, "width": 0.3},
                 "remove_label": {"label_id": "l001"}, "replace_timeline_clip": {"scene_id": "sc_001", "clip_index": 0, "new_source_id": "src_01", "start_time": 2.0},
                 "adjust_pacing_and_transitions": {"chapter": "all", "max_clip_duration": 3}, "render_rough_cut": {},
                 "sync_to_capcut_draft": {"capcut_drafts_dir": str(self.outside)}, "get_frame": {"time": 0}}
        for name, args in calls.items():
            with self.subTest(name, project=str(project)):
                failed, text = self.call(name, project=str(project), **args)
                self.assertTrue(failed)
                self.assertIn(why, text)
        self.assertEqual(_snapshot(self.outside), self.before)

    def test_default_root_project_is_usable(self):
        failed, text = self.call("inspect_timeline_state", project=str(self.run))
        self.assertFalse(failed, text)
        failed, text = self.call("apply_smart_label_overlay", project=str(self.run), scene_id="sc_001", text_en="Spillway", pos_x=0.5, pos_y=0.5, width=0.3)
        self.assertFalse(failed, text)
        self.assertEqual([label["text"] for label in roughcut.load_labels(self.run)], ["Spillway"])

    def test_custom_root_is_bound_by_list_projects(self):
        custom = make_run(Path(self.temp.name) / "D drive")
        _planned(custom)
        failed, text = self.call("inspect_timeline_state", project=str(custom))
        self.assertTrue(failed)
        self.assertIn(OUTSIDE, text)
        failed, text = self.call("list_projects", root=str(custom.parent.parent))
        self.assertFalse(failed, text)
        self.assertEqual([item["project"] for item in json.loads(text)], [str(custom.resolve())])
        self.assertFalse(self.call("inspect_timeline_state", project=str(custom))[0])
        self.assertFalse(self.call("inspect_timeline_state", project=str(self.run))[0])
        listed = json.loads(self.call("list_projects")[1])
        self.assertEqual({item["project"] for item in listed}, {str(custom.resolve()), str(self.run.resolve())})
        for bad in ("relative\\DubFlow Output", str(Path(self.temp.name) / "missing")):
            with self.subTest(bad):
                self.assertTrue(self.call("list_projects", root=bad)[0])
        self.assertEqual(self.server.chosen, [custom.parent.parent.resolve()])

    def test_out_of_root_project_is_refused(self):
        self.refused(self.outside_run, OUTSIDE)
        # The same folder spelled from inside the root.
        self.refused(self.root / ".." / ".." / "elsewhere" / "DubFlow Output" / self.outside_run.parent.name / self.outside_run.name, OUTSIDE)

    def test_linked_title_and_run_folders_are_refused_and_not_listed(self):
        title = self.root / "Phim tai lieu - Ext"
        _link_dir(title, self.outside_run.parent)
        self.refused(title / self.outside_run.name, OUTSIDE)
        run = self.run.parent / "run-00000000000a"
        _link_dir(run, self.outside_run)
        self.refused(run, OUTSIDE)
        alias = self.run.parent / "run-00000000000b"
        _link_dir(alias, self.run)
        self.refused(alias, LINKED)
        for args in ({}, {"root": str(self.root)}):
            with self.subTest(args):
                listed = json.loads(self.call("list_projects", **args)[1])
                self.assertEqual([item["project"] for item in listed], [str(self.run.resolve())])
        self.assertEqual(_snapshot(self.outside), self.before)

    def test_linked_work_folder_is_refused(self):
        run = self.run.parent / "run-00000000000c"
        run.mkdir()
        _planned(run)
        _link_dir(work_dir(run), work_dir(self.outside_run))
        self.refused(run, LINKED)
        listed = json.loads(self.call("list_projects")[1])
        self.assertEqual([item["project"] for item in listed], [str(self.run.resolve())])

    def test_linked_mutable_files_are_refused(self):
        for child in (work_dir(self.run) / roughcut.LABELS, work_dir(self.run) / (roughcut.LABELS + ".partial"),
                      self.run / "visual_plan.json", work_dir(self.run) / visual.PACING):
            original = child.read_bytes() if child.is_file() else None
            child.unlink(missing_ok=True)
            child.symlink_to(self.marker)
            self.refused(self.run, LINKED)
            child.unlink()
            if original is not None:
                child.write_bytes(original)
        render = work_dir(self.run) / "render"
        (self.outside / "render").mkdir()
        self.before = _snapshot(self.outside)
        _link_dir(render, self.outside / "render")
        self.refused(self.run, LINKED)

    def test_hard_linked_partial_is_dropped_before_writing(self):
        partial = work_dir(self.run) / (roughcut.LABELS + ".partial")
        os.link(self.marker, partial)
        failed, text = self.call("apply_smart_label_overlay", project=str(self.run), scene_id="sc_001", text_en="Spillway", pos_x=0.5, pos_y=0.5, width=0.3)
        self.assertFalse(failed, text)
        self.assertEqual(_snapshot(self.outside), self.before)
        self.assertEqual(len(roughcut.load_labels(self.run)), 1)

    def test_reparse_points_that_name_another_place(self):
        point = stat.FILE_ATTRIBUTE_REPARSE_POINT
        cases = {"junction": (stat.S_IFDIR, point, 0xA0000003, True), "symlink reparse": (stat.S_IFREG, point, 0xA000000C, True),
                 "posix symlink": (stat.S_IFLNK, 0, 0, True), "OneDrive placeholder": (stat.S_IFREG, point, 0x9000601A, False),
                 "app alias": (stat.S_IFREG, point, 0x8000001B, False), "folder": (stat.S_IFDIR, stat.FILE_ATTRIBUTE_DIRECTORY, 0, False)}
        for name, (mode, attributes, tag, expected) in cases.items():
            with self.subTest(name):
                info = SimpleNamespace(st_mode=mode, st_file_attributes=attributes, st_reparse_tag=tag)
                self.assertIs(mcp_tools.redirects(info), expected)
        if os.name == "nt":
            # A real junction, as scandir and lstat report it on Windows.
            target = Path(self.temp.name) / "target"
            target.mkdir()
            _link_dir(Path(self.temp.name) / "junction", target)
            self.assertTrue(mcp_tools.linked(Path(self.temp.name) / "junction"))
            with os.scandir(self.temp.name) as entries:
                entry = next(item for item in entries if item.name == "junction")
                self.assertTrue(mcp_tools.redirects(entry.stat(follow_symlinks=False)))
        self.assertFalse(mcp_tools.linked(self.run))


class ProtocolTests(unittest.TestCase):
    def test_handshake_tools_and_errors_over_stdio(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            _planned(run)
            server = Server(mcp_tools.Install(Path(temp), Path("ffmpeg"), Path("ffprobe"), Path(temp)), roots=lambda: [run.parent.parent], activity=False)
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
            self.assertEqual(len(TOOLS), 19)
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


class ActivityTests(unittest.TestCase):
    """What the desktop app shows while Claude works over MCP."""

    def test_calls_are_written_for_the_app(self):
        from engine.dubflow.mcp.activity import Activity, default_path, label
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            _planned(run)
            path = Path(temp) / "control" / "mcp-activity.json"
            server = Server(mcp_tools.Install(Path(temp), Path("ffmpeg"), Path("ffprobe"), Path(temp)), roots=lambda: [run.parent.parent],
                            activity=path)
            seen = []
            original = server.call

            def call(name, args):
                seen.append(json.loads(path.read_text(encoding="utf-8"))["current"])
                return original(name, args)
            server.call = call
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "replace_timeline_clip", "arguments": {
                "project": str(run), "scene_id": "sc_001", "clip_index": 1, "new_source_id": "src_01", "start_time": 12.0}}})
            self.assertEqual(seen[0]["label"], "Thay lát cắt sc_001 #1 bằng src_01 từ 12.0 s")
            self.assertEqual(seen[0]["project"], str(run))
            server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "inspect_timeline_state", "arguments": {"project": "C:/nope"}}})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(saved["current"])
            self.assertEqual([item["state"] for item in saved["recent"]], ["failed", "done"])
            self.assertIn("Dựng lại bản nháp", saved["recent"][1]["message"])
            # A new server keeps the history the app shows.
            self.assertEqual(len(Activity(path).recent), 2)
        self.assertEqual(default_path(Path("data") / "models"), Path("data") / "control" / "mcp-activity.json")
        self.assertEqual(label("narrate", {"voice": "am_michael"}), "Tạo lời đọc (giọng am_michael)")

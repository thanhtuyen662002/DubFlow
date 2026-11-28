"""MCP Chỉnh sửa tools (#1002, ADR-0059): the editor's own draft, completed and handed to the app, and its answers."""
from __future__ import annotations

import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu.project import DocuError
from engine.dubflow.mcp import editing

MODEL_TS = Path(__file__).resolve().parents[2] / "apps" / "desktop" / "src" / "features" / "edit" / "model.ts"
FONTS_TS = MODEL_TS.with_name("fonts.ts")
FACTS = {"duration": 60.0, "width": 1080, "height": 1920, "fps": 30.0, "hasAudio": True}


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        folder = self.root / "DubFlow Output" / "Đập Tam Hiệp - vi" / "run-0123456789ab"
        (folder / ".dubflow-work").mkdir(parents=True)
        (folder / ".dubflow-work" / "translation.json").write_text(json.dumps({"cues": [
            {"cue_id": "c1", "start_ms": 1000, "end_ms": 3000, "translated_text": "Con đập", "source_text": "大坝"}]}), encoding="utf-8")
        (folder / "final_vi.mp4").write_bytes(b"\x00")
        self.video = folder / "final_vi.mp4"
        self.broll = self.root / "broll.mp4"
        self.broll.write_bytes(b"\x00")
        self.music = self.root / "nhac.mp3"
        self.music.write_bytes(b"\x00")
        self.data = self.root / "data"
        self.probed = []

    def tearDown(self):
        self.temp.cleanup()

    def probe(self, path):
        self.probed.append(Path(path))
        if Path(path) == self.music:
            return {"duration": 125.5, "width": 0, "height": 0, "fps": 0, "hasAudio": True}
        if Path(path) == self.broll:
            return {"duration": 12.0, "width": 1920, "height": 1080, "fps": 25.0, "hasAudio": False}
        return dict(FACTS)

    def test_text_defaults_and_lists_match_the_editor(self):
        model = MODEL_TS.read_text(encoding="utf-8")
        look = re.search(r"export const DEFAULT_TEXT_LOOK: TextLook = \{(.*?)\};", model, re.S).group(1)
        parsed = dict(re.findall(r"(\w+): (\"[^\"]*\"|[\w.]+)", look))
        expected = {key: json.loads(value) if value not in ("true", "false", "null") else {"true": True, "false": False, "null": None}[value]
                    for key, value in parsed.items()}
        self.assertEqual(editing.TEXT_LOOK, expected)
        self.assertEqual(list(editing.TRANSITIONS), re.findall(r'"(\w+)"', re.search(r"TRANSITION_TYPES = \[(.*?)\]", model).group(1)))
        fonts = re.search(r"SUBTITLE_FONTS = \[(.*?)\] as const", FONTS_TS.read_text(encoding="utf-8"), re.S).group(1)
        self.assertEqual(list(editing.FONTS), re.findall(r'"([^"]+)"', fonts))

    def test_get_edit_reads_the_draft_or_starts_one(self):
        fresh = editing.get_edit({"video": str(self.video)}, ffprobe=Path("ffprobe"))
        self.assertIn("guide", fresh)
        self.assertEqual(fresh["subtitles"][0]["text"], "Con đập")
        draft_path = self.video.parent / ".dubflow-work" / "edit" / "project.json"
        draft_path.parent.mkdir(parents=True)
        draft_path.write_text(json.dumps({"schema_version": 4, "segments": [{"id": "s1"}], "undo": {"past": [1] * 100}}), encoding="utf-8")
        got = editing.get_edit({"video": str(self.video)}, ffprobe=Path("ffprobe"))
        self.assertEqual(got["draft"]["segments"], [{"id": "s1"}])
        self.assertNotIn("undo", got["draft"])
        start = editing.starter(self.video, FACTS)
        self.assertEqual(start["segments"], [{"id": "s1", "mediaId": "main", "start": 0, "end": 60.0, "speed": 1, "volume": 1}])
        self.assertEqual((start["media"][0]["id"], start["media"][0]["kind"], start["next_id"]), ("main", "main", 2))

    def test_save_completes_the_draft_and_writes_an_edit_request(self):
        draft = editing.starter(self.video, FACTS)
        draft["segments"] = [{"id": "s1", "mediaId": "main", "start": 2, "end": 20, "speed": 1, "volume": 1},
                             {"id": "s3", "mediaId": "m2", "start": 0, "end": 5, "speed": 1, "volume": 0}]
        draft["media"].append({"id": "m2", "path": str(self.broll)})
        draft["transitions"] = {"s1": {"type": "dissolve", "duration": 0.5}}
        draft["texts"] = [{"id": "t4", "text": "Đập Tam Hiệp", "at": 0.5, "duration": 3, "size": 90}]
        draft["audio"] = [{"id": "a5", "source": {"kind": "file", "path": str(self.music)}, "volume": 0.2, "loop": True}]
        draft["next_id"] = 6
        made = editing.save_edit({"video": str(self.video), "draft": draft, "export": True, "file_name": "Đập (agent)", "quality": "1080p"},
                                 data_root=self.data, probe=self.probe, now=lambda: 1_795_000_000.0, token=lambda: "a1b2c3d4")
        self.assertEqual(made["state"], "WAITING_FOR_APP")
        request = json.loads((self.data / "control" / "inbox" / f"{made['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual((request["kind"], request["schema_version"], request["output_path"]), ("edit", 1, str(self.video)))
        self.assertEqual(request["export"], {"file_name": "Đập (agent)", "quality": "1080p"})
        project = request["project"]
        self.assertEqual(project["schema_version"], 4)
        self.assertEqual(project["media"][1], {"id": "m2", "path": str(self.broll), "title": "broll.mp4", "duration": 12.0, "width": 1920,
                                               "height": 1080, "fps": 25.0, "hasAudio": False, "kind": "file"})
        self.assertEqual(project["media"][0]["kind"], "main")
        text = project["texts"][0]
        self.assertEqual((text["size"], text["font"], text["align"], text["box"]), (90, "Arial", "center", None))
        sound = project["audio"][0]
        self.assertEqual((sound["duration"], sound["end"], sound["start"], sound["title"], sound["fade_in"]), (125.5, 125.5, 0, "nhac.mp3", 0))
        self.assertIn(self.music, self.probed)

    def test_bad_drafts_are_refused_before_the_inbox(self):
        good = editing.starter(self.video, FACTS)
        for draft, words in (({**good, "segments": []}, "ít nhất một đoạn"),
                             ({**good, "media": [*good["media"], {"id": "m2", "path": str(self.root / "none.mp4")}]}, "có thật"),
                             ({**good, "media": [*good["media"], {"id": "b-roll", "path": str(self.broll)}]}, "m<số>"),
                             ({**good, "audio": [{"id": "a2", "source": {"kind": "file", "path": "nhac.mp3"}}]}, "đường dẫn đầy đủ")):
            with self.subTest(words=words), self.assertRaises(DocuError) as caught:
                editing.save_edit({"video": str(self.video), "draft": draft}, data_root=self.data, probe=self.probe)
            self.assertIn(words, str(caught.exception))
        for args in ({"draft": good, "export": True, "file_name": "..\\x"}, {"draft": good, "export": True, "quality": "4k"}, {"draft": [1]}):
            with self.subTest(args=args), self.assertRaises(DocuError):
                editing.save_edit({"video": str(self.video), **args}, data_root=self.data, probe=self.probe)
        with self.assertRaises(DocuError):
            editing.save_edit({"video": str(self.broll), "draft": good}, data_root=self.data, probe=self.probe)
        self.assertFalse((self.data / "control" / "inbox").exists())

    def test_answers_from_waiting_to_completed(self):
        made = editing.save_edit({"video": str(self.video), "draft": editing.starter(self.video, FACTS), "export": True, "file_name": "Bản agent"},
                                 data_root=self.data, probe=self.probe)
        job_id, inbox = made["id"], self.data / "control" / "inbox"
        self.assertEqual(editing.edit_job(job_id, data_root=self.data)["state"], "WAITING_FOR_APP")
        (inbox / "accepted").mkdir()
        (inbox / f"{job_id}.json").rename(inbox / "accepted" / f"{job_id}.json")
        self.assertEqual(editing.edit_job(job_id, data_root=self.data)["state"], "IN_APP")
        (inbox / "results").mkdir()
        answer = inbox / "results" / f"{job_id}.json"
        answer.write_text(json.dumps({"state": "EXPORTING", "message": "Đang xuất", "file_name": "Bản agent", "dropped": {"texts": 1}}), encoding="utf-8")
        status = self.video.parent / ".dubflow-work" / "edit" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps({"state": "COMPLETED", "output_path": str(self.root / "Đập (sửa 1).mp4")}), encoding="utf-8")
        waiting = editing.edit_job(job_id, data_root=self.data)
        self.assertEqual((waiting["state"], waiting["dropped"]), ("EXPORTING", {"texts": 1}), "an earlier export's result is not this one's")
        status.write_text(json.dumps({"state": "RUNNING", "percent": 42, "message": "Đang xuất 42%"}), encoding="utf-8")
        self.assertEqual(editing.edit_job(job_id, data_root=self.data)["percent"], 42)
        status.write_text(json.dumps({"state": "COMPLETED", "message": "Xong", "output_path": str(self.root / "Bản agent.mp4")}), encoding="utf-8")
        done = editing.edit_job(job_id, data_root=self.data)
        self.assertEqual((done["state"], done["output"]), ("COMPLETED", str(self.root / "Bản agent.mp4")))
        answer.write_text(json.dumps({"state": "REJECTED", "message": "Video này đang mở trong Chỉnh sửa"}), encoding="utf-8")
        self.assertEqual(editing.edit_job(job_id, data_root=self.data)["state"], "REJECTED")
        for bad in ("job-1", "mcp-20261118-000000-ffffffff"):
            with self.subTest(bad=bad), self.assertRaises(DocuError):
                editing.edit_job(bad, data_root=self.data)


if __name__ == "__main__":
    unittest.main()

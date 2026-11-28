"""MCP dubbing tools (#998, ADR-0058): requests the app's queue takes, and their progress from the supervisor's status."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.docu.project import DocuError
from engine.dubflow.mcp import dubbing

VOICES = [{"voice_id": "vi-a"}, {"voice_id": "vi-b"}]


class DubbingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.video = self.root / "Nguồn" / "clip.mp4"
        self.video.parent.mkdir()
        self.video.write_bytes(b"\x00")
        self.inbox = self.data / "control" / "inbox"

    def tearDown(self):
        self.temp.cleanup()

    def queue(self, **overrides):
        args = {"video": str(self.video), **overrides}
        return dubbing.queue_dubbing_job(args, data_root=self.data, voices=lambda: VOICES, now=lambda: 1_795_000_000.0,
                                         token=lambda: "a1b2c3d4")

    def test_a_request_in_the_inbox_with_only_the_options_given(self):
        made = self.queue(voice_id="vi-b", source_language="zh", blur_captions=True, translation="offline", title="  Đập\n Tam Hiệp ")
        self.assertEqual(made["id"], "mcp-20261118-110640-a1b2c3d4")
        self.assertEqual(made["state"], "WAITING_FOR_APP")
        request = json.loads((self.inbox / f"{made['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual({key: request[key] for key in ("schema_version", "kind", "id", "source_path", "title")},
                         {"schema_version": 1, "kind": "dubbing", "id": made["id"], "source_path": str(self.video), "title": "Đập Tam Hiệp"})
        self.assertEqual(request["options"], {"dub": None, "voice_id": "vi-b", "source_language": "zh", "burn_in": None, "blur_auto": True,
                                              "separate_background": None, "multi_voice": None, "translation_engine": "argos"})
        self.assertRegex(made["id"], dubbing.ID)
        self.assertEqual(list(self.inbox.glob("*.partial")), [])

    def test_bad_requests_are_refused_before_the_inbox(self):
        for overrides, words in (({"video": "clip.mp4"}, "đường dẫn đầy đủ"), ({"video": str(self.root / "none.mp4")}, "có thật"),
                                 ({"voice_id": "vi-x"}, "list_vietnamese_voices"), ({"source_language": "fr"}, "source_language"),
                                 ({"translation": "google"}, "translation"), ({"dub": "yes"}, "dub")):
            with self.subTest(overrides=overrides), self.assertRaises(DocuError) as caught:
                self.queue(**overrides)
            self.assertIn(words, str(caught.exception))
        text = self.root / "note.txt"
        text.write_text("x", encoding="utf-8")
        with self.assertRaises(DocuError):
            self.queue(video=str(text))
        self.assertFalse(self.inbox.exists())

    def test_states_from_waiting_to_completed(self):
        job_id = self.queue()["id"]
        self.assertEqual(dubbing.dubbing_job(job_id, data_root=self.data)["state"], "WAITING_FOR_APP")
        (self.inbox / "accepted").mkdir()
        (self.inbox / f"{job_id}.json").rename(self.inbox / "accepted" / f"{job_id}.json")
        queued = dubbing.dubbing_job(job_id, data_root=self.data)
        self.assertEqual((queued["state"], queued["video"]), ("QUEUED", str(self.video)))
        out = self.root / "DubFlow Output" / "clip - vi" / "run-0123456789ab"
        out.mkdir(parents=True)
        for name in ("final_vi.mp4", "final_vi.srt", "notes.json"):
            (out / name).write_bytes(b"x")
        status = self.data / "control" / "jobs" / f"{job_id}.json"
        status.parent.mkdir(parents=True)
        status.write_text(json.dumps({"job_id": job_id, "output_path": None, "status": {
            "state": "RUNNING", "message": "Đang dịch", "progress": {"completed_units": "250", "total_units": "1000"}}}), encoding="utf-8")
        running = dubbing.dubbing_job(job_id, data_root=self.data)
        self.assertEqual((running["state"], running["percent"], running["message"]), ("RUNNING", 25.0, "Đang dịch"))
        status.write_text(json.dumps({"job_id": job_id, "output_path": str(out / "final_vi.mp4"), "status": {
            "state": "COMPLETED", "reason": "completed", "progress": {"completed_units": "1000", "total_units": "1000"}}}), encoding="utf-8")
        done = dubbing.dubbing_job(job_id, data_root=self.data)
        self.assertEqual((done["state"], done["percent"], done["video_out"]), ("COMPLETED", 100.0, str(out / "final_vi.mp4")))
        self.assertEqual(done["files"], [str(out / "final_vi.mp4"), str(out / "final_vi.srt")])

    def test_a_refused_request_says_why_and_unknown_ids_are_errors(self):
        job_id = self.queue()["id"]
        (self.inbox / "rejected").mkdir()
        (self.inbox / f"{job_id}.json").rename(self.inbox / "rejected" / f"{job_id}.json")
        (self.inbox / "rejected" / f"{job_id}.json.reason.txt").write_text("source video is missing", encoding="utf-8")
        refused = dubbing.dubbing_job(job_id, data_root=self.data)
        self.assertEqual((refused["state"], refused["message"]), ("REJECTED", "source video is missing"))
        for bad in ("job-0123", "mcp-../../x", "", "mcp-20261118-000000-ffffffff"):
            with self.subTest(bad=bad), self.assertRaises(DocuError):
                dubbing.dubbing_job(bad, data_root=self.data)

    def test_list_is_newest_first(self):
        first = dubbing.queue_dubbing_job({"video": str(self.video)}, data_root=self.data, voices=lambda: VOICES, now=lambda: 1_795_000_000.0,
                                          token=lambda: "00000001")["id"]
        second = dubbing.queue_dubbing_job({"video": str(self.video)}, data_root=self.data, voices=lambda: VOICES, now=lambda: 1_795_000_060.0,
                                           token=lambda: "00000002")["id"]
        (self.inbox / "stray.json").write_text("{}", encoding="utf-8")
        self.assertEqual([job["id"] for job in dubbing.list_dubbing_jobs(data_root=self.data)], [second, first])
        self.assertEqual(dubbing.list_dubbing_jobs(data_root=self.root / "elsewhere"), [])

    def test_tool_schemas(self):
        names = [tool["name"] for tool in dubbing.TOOLS]
        self.assertEqual(names, ["queue_dubbing_job", "get_dubbing_job", "list_dubbing_jobs"])
        self.assertEqual(dubbing.TOOLS[0]["inputSchema"]["required"], ["video"])
        self.assertFalse(dubbing.TOOLS[0]["annotations"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()

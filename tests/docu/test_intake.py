from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.dubflow.docu import intake, transcribe
from engine.dubflow.docu.project import DocuError, load_spec, script_path, spec_path
from engine.dubflow.mcp.server import TOOLS


def fake_audio(argv, **kwargs):
    Path(argv[-1]).write_bytes(b"RIFF")
    return SimpleNamespace(returncode=0)


def recognise(audio, **kwargs):
    cue = SimpleNamespace(cue_id="c1", start_ms=1000, end_ms=4000, source_text="这座大楼有26层，建筑面积40萬平方米")
    kwargs["on_chunk"](1, 1, "chunk", "hash")
    return SimpleNamespace(cues=[cue])


class RunRecordTests(unittest.TestCase):
    def test_record_uses_the_app_key_and_keeps_an_existing_record(self):
        with TemporaryDirectory() as temp:
            data, run = Path(temp) / "data", Path(temp) / "DubFlow Output" / "Phim tai lieu - Dam's $5M" / "run-74ac8002faf2"
            run.mkdir(parents=True)
            intake.record_run(data, run)
            folder = run.resolve()
            key = hashlib.sha256(str(folder).lower().encode("utf-8")).digest()[:10].hex()
            record = data / "control" / "runs" / f"{key}.json"
            self.assertEqual(json.loads(record.read_text(encoding="utf-8")), {"schema_version": 1, "kind": "docu", "dir": str(folder)})
            record.write_text("{}", encoding="utf-8")
            intake.record_run(data, run)
            self.assertEqual(record.read_text(encoding="utf-8"), "{}")


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.videos = []
        for name in ("a.mp4", "b.mp4"):
            video = self.base / "src" / name
            video.parent.mkdir(exist_ok=True)
            video.write_bytes(b"video " + name.encode())
            self.videos.append(video)
        (self.base / "src" / "b.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\n总投资40亿元\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **changes):
        args = dict(title="Hubei: Pig Tower?", topic="pig skyscraper", target_minutes=6, framing="letterbox_235", burn_end_credits=True,
                    sources=[{"video": str(self.videos[0]), "url": "https://www.bilibili.com/video/BV1Vy4y1Z7C1", "creator": "海报视频"},
                             {"video": str(self.videos[1]), "subtitles": str(self.base / "src" / "b.srt"), "title": "Second"}],
                    app_root=self.base, model_root=self.base, ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), base=self.base,
                    probe=lambda video: 60_000, recognise=recognise)
        args.update(changes)
        with patch.object(transcribe.subprocess, "run", fake_audio):
            return intake.create_project(**args)

    def test_a_project_from_plain_videos(self):
        # 2026-11-04: a documentary source had to be a finished Vietsub job (237 MB render) just for its transcript.
        steps = []
        created = self.create(progress=steps.append)
        run = Path(created["project"])
        self.assertEqual(run.parent.name, "Phim tai lieu - Hubei Pig Tower")
        self.assertRegex(run.name, r"^run-[0-9a-f]{12}$")
        spec = load_spec(spec_path(run))
        self.assertEqual(spec.cinematic_framing_preset, "letterbox_235")
        self.assertEqual(spec.sources[0].transcript, run / ".dubflow-work" / "sources" / "src_01" / "transcript.json")
        self.assertEqual(spec.sources[0].credit["platform"], "Bilibili")
        self.assertEqual(spec.sources[1].transcript.suffix, ".srt")
        self.assertEqual([item["segments"] for item in created["sources"]], [1, 1])
        self.assertEqual(len(steps), 1, "only the source without subtitles is recognised")
        saved = json.loads(spec.sources[0].transcript.read_text(encoding="utf-8"))
        self.assertEqual(saved["cues"][0]["source_text"], "这座大楼有26层，建筑面积40萬平方米")
        self.assertFalse((spec.sources[0].transcript.parent / "analysis.wav").exists())
        # The same video again: the transcript is reused, nothing is recognised.
        out = spec.sources[0].transcript.parent
        again = transcribe.transcribe(self.videos[0], out, app_root=self.base, model_root=self.base, ffmpeg=Path("ffmpeg"),
                                      recognise=lambda *a, **k: self.fail("recognised again"))
        self.assertEqual(again, spec.sources[0].transcript)

        brief = intake.script_brief(run)
        self.assertEqual([chapter["word_budget"] for chapter in brief["chapters"]], [63, 180, 450, 207])
        self.assertIn("[src_01:1.0-4.0] 这座大楼有26层", brief["source_lines"])
        self.assertIn("40万平方米 = 98.84 acres", brief["quantity_facts"])

        draft = {"project_title": "Pig Tower", "chapters": [{"chapter_type": kind, "scenes": [{
            "narration_text": "The tower has a floor area of about 98.8 acres, and it cost 9 billion dollars.",
            "matched_source_refs": [{"source_id": "src_01", "start": 1, "end": 4}]}]} for kind in intake.CHAPTERS]}
        saved = intake.save_script(run, draft)
        self.assertEqual(saved["scenes"], 4)
        self.assertIn("9 billion dollars", saved["needs_review"][0]["notes"][0])
        self.assertTrue(script_path(run).is_file())

    def test_bad_requests_are_refused(self):
        for changes, words in (({"framing": "wide"}, "framing"), ({"sources": [{"video": str(self.videos[0])}]}, "2 đến 15"),
                               ({"sources": [{"video": "relative.mp4"}, {"video": str(self.videos[1])}]}, "Nguồn 1"),
                               ({"sources": [{"video": str(self.videos[0]), "subtitles": str(self.videos[1])}, {"video": str(self.videos[1])}]}, ".srt")):
            with self.subTest(words), self.assertRaises(DocuError) as caught:
                self.create(**changes)
            self.assertIn(words, caught.exception.message)

    def test_downloads_go_through_the_verified_adapter(self):
        self.assertEqual(intake.provider_of("https://www.bilibili.com/video/BV1QdFzz7E47"), "bilibili")
        self.assertEqual(intake.provider_of("https://v.douyin.com/abc/"), "douyin")
        self.assertEqual(intake.provider_of("https://example.com/a.mp4"), "generic")
        with self.assertRaises(DocuError):
            intake.provider_of("file:///C:/a.mp4")
        calls = []

        class Adapter:
            def inspect(self, url):
                return SimpleNamespace(identity=SimpleNamespace(source_id="BV1QdFzz7E47"), title="养猪大楼", duration_ticks=287320)

            def download(self, item, destination, **kwargs):
                calls.append(kwargs)
                destination.write_bytes(b"mp4")
                return SimpleNamespace(size_bytes=3)
        result = intake.download("https://www.bilibili.com/video/BV1QdFzz7E47", version_root=self.base, data_root=self.base,
                                 folder=self.base / "Sources", adapter_factory=lambda provider: Adapter())
        self.assertEqual(result["video"], str(self.base / "Sources" / "BV1QdFzz7E47" / "source-media.mp4"))
        self.assertEqual(result["duration_s"], 287.32)
        self.assertEqual(json.loads((self.base / "Sources" / "BV1QdFzz7E47" / "info.json").read_text(encoding="utf-8"))["title"], "养猪大楼")
        self.assertFalse(calls[0]["resume"])

    def test_the_whole_flow_is_in_the_tool_list(self):
        names = [tool["name"] for tool in TOOLS]
        for name in ("discover_videos", "download_video", "create_project", "get_script_brief", "save_script", "narrate", "plan_visuals"):
            self.assertIn(name, names)
        with self.assertRaises(DocuError):
            intake.discover("youtube", "x")


if __name__ == "__main__":
    unittest.main()

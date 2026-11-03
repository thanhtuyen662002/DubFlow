from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.docu import capcut_draft, script
from engine.dubflow.docu.project import DocuError, load_spec, spec_path, work_dir, write_json

from test_docu import make_run


def _rendered(run: Path, frames: list[int], *, card: bool = False) -> None:
    """What roughcut.run leaves: slices with receipts, the concat list, the film, narration and timeline."""
    render = work_dir(run) / "render"
    render.mkdir(parents=True, exist_ok=True)
    names = []
    for index, count in enumerate(frames, 1):
        name = f"slice-{index:04d}.mp4"
        (render / name).write_bytes(b"mp4")
        write_json(render / f"slice-{index:04d}.json", {"key": {"frames": count}, "encoder": "h264_mf"})
        names.append(name)
    if card:
        (render / "card.mp4").write_bytes(b"mp4")
        names.append("card.mp4")
    (render / "concat.txt").write_text("".join(f"file '{name}'\n" for name in names), encoding="utf-8")
    (run / "documentary.mp4").write_bytes(b"mp4")
    write_json(run / "visual_plan.json", {"scenes": []})
    write_json(run / "master_audio_timeline.json", {"duration": sum(frames) / 30})
    write_json(run / "documentary_script.json", {"project_title": "How a Dam Defies Physics", "chapters": []})
    with wave.open(str(run / "narration.wav"), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 4800)


def _capcut(base: Path) -> Path:
    root = base / "com.lveditor.draft"
    root.mkdir()
    write_json(root / "root_meta_info.json", {"all_draft_store": [{"draft_name": "old", "draft_fold_path": "x"}], "draft_ids": 3,
                                             "root_path": root.as_posix()})
    return root


class TemplateTests(unittest.TestCase):
    def test_template_has_no_machine_identifiers(self):
        text = capcut_draft.TEMPLATE.read_text(encoding="utf-8")
        value = json.loads(text)
        self.assertEqual(value["version"], 360000)
        for platform in (value["content"]["platform"], value["content"]["last_modified_platform"]):
            self.assertEqual((platform["device_id"], platform["mac_address"], platform["hard_disk_id"]), ("", "", ""))
        for needle in ("C:/Users", "D:/Projects", "AppData"):
            self.assertNotIn(needle, text)

    def test_build_is_frame_exact_and_references_resolve(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            clips = [(root / "slice-0001.mp4", 0, 123), (root / "slice-0002.mp4", 123, 90), (root / "card.mp4", 213, 180)]
            content, meta, entry = capcut_draft.build(clips, (root / "narration.wav", 7.1), title="T", folder=root / "DubFlow - T",
                                                      root=root, now_micro=1_793_000_000_000_000)
            video, audio = content["tracks"]
            self.assertEqual((video["type"], audio["type"]), ("video", "audio"))
            starts = [segment["target_timerange"]["start"] for segment in video["segments"]]
            lengths = [segment["target_timerange"]["duration"] for segment in video["segments"]]
            self.assertEqual(starts, [0, 4_100_000, 7_100_000])
            self.assertEqual(lengths, [4_100_000, 3_000_000, 6_000_000])
            self.assertEqual(content["duration"], 13_100_000)
            ids = {item["id"] for items in content["materials"].values() for item in items}
            for segment in video["segments"] + audio["segments"]:
                self.assertIn(segment["material_id"], ids)
                self.assertTrue(set(segment["extra_material_refs"]) <= ids)
            self.assertEqual(content["materials"]["videos"][0]["path"], (root / "slice-0001.mp4").as_posix())
            self.assertEqual(audio["segments"][0]["target_timerange"], {"start": 0, "duration": 7_100_000})
            self.assertEqual((meta["draft_id"], entry["draft_id"]), (content["id"], content["id"]))
            self.assertEqual(len(meta["draft_materials"][0]["value"]), 4)
            self.assertEqual(entry["draft_json_file"], (root / "DubFlow - T" / "draft_content.json").as_posix())

    def test_folder_names_never_overwrite(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(capcut_draft.folder_name('A: "dam"?', root), "DubFlow - A dam")
            (root / "DubFlow - A dam").mkdir()
            self.assertEqual(capcut_draft.folder_name('A: "dam"?', root), "DubFlow - A dam (2)")


class ExportTests(unittest.TestCase):
    def test_export_writes_links_and_registers(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_run(base)
            spec = load_spec(spec_path(run))
            _rendered(run, [123, 90], card=True)
            root = _capcut(base)
            result = capcut_draft.export(run, spec, root=root, running=lambda: False, clock=lambda: 1_793_000_000.0)
            folder = Path(result["folder"])
            self.assertEqual(folder.name, "DubFlow - How a Dam Defies Physics")
            content = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
            self.assertEqual(len(content["tracks"][0]["segments"]), 3)
            self.assertTrue((run / capcut_draft.MEDIA / "slice-0002.mp4").is_file(), "the draft's media live with the project")
            self.assertTrue(content["materials"]["videos"][0]["path"].endswith(f"{capcut_draft.MEDIA}/slice-0001.mp4"))
            for name in ("draft_meta_info.json", "draft_settings", "timeline_layout.json"):
                self.assertTrue((folder / name).is_file(), name)
            index = json.loads((root / "root_meta_info.json").read_text(encoding="utf-8"))
            self.assertEqual([item["draft_name"] for item in index["all_draft_store"]], [folder.name, "old"])
            self.assertEqual(index["draft_ids"], 4)
            backup = json.loads((root / "root_meta_info.json.dubflow-backup").read_text(encoding="utf-8"))
            self.assertEqual(backup["draft_ids"], 3)
            self.assertFalse(list(root.glob(".*dubflow-partial")))
            self.assertEqual(json.loads((work_dir(run) / "capcut.json").read_text(encoding="utf-8"))["clips"], 3)
            # A second export is a second draft; nothing is overwritten.
            again = capcut_draft.export(run, spec, root=root, running=lambda: False)
            self.assertEqual(Path(again["folder"]).name, "DubFlow - How a Dam Defies Physics (2)")

    def test_refusals(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            run = make_run(base)
            spec = load_spec(spec_path(run))
            root = _capcut(base)
            with self.assertRaises(DocuError) as caught:
                capcut_draft.export(run, spec, root=root, running=lambda: True)
            self.assertIn("đóng CapCut", caught.exception.message)
            with self.assertRaises(DocuError):
                capcut_draft.export(run, spec, root=root, running=lambda: False)
            _rendered(run, [30])
            with self.assertRaises(DocuError):
                capcut_draft.export(run, spec, root=base / "missing", running=lambda: False)
            (root / "root_meta_info.json").write_text("{", encoding="utf-8")
            with self.assertRaises(DocuError):
                capcut_draft.export(run, spec, root=root, running=lambda: False)


if __name__ == "__main__":
    unittest.main()

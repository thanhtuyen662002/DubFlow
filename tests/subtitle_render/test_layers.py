"""Tách phụ đề: a plain video's words and their Vietnamese as two layers, the AI pass optional."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.dubflow.separation.__main__ import Status
from engine.dubflow.subtitle import layers
from engine.dubflow.translation.sensitive import SensitivePolicy


def transcript(video, out_dir, **options):
    options["progress"]("asr", 1, 2)
    path = Path(out_dir) / "transcript.json"
    path.write_text(json.dumps({"cues": [
        {"cue_id": "c1", "start_ms": 0, "end_ms": 1500, "source_text": "他死了"},
        {"cue_id": "c2", "start_ms": 1600, "end_ms": 3000, "source_text": "我们走吧"},
        {"cue_id": "c3", "start_ms": 3000, "end_ms": 3000, "source_text": "坏"},
    ]}), encoding="utf-8")
    return path


def offline(cues, **_):
    return [replace(cue, translated_text={"c1": "Anh ta chết rồi", "c2": "Chúng ta đi thôi"}[cue.cue_id]) for cue in cues]


@dataclass
class Settings:
    route: object = None
    sensitive: object = None
    title: str = ""


@dataclass
class Result:
    cues: tuple
    cues_from_ai: int
    error: str | None = None


class Route:
    class preset:
        label = "Gemini"


class LayersTest(unittest.TestCase):
    def run_layers(self, folder: Path, **options) -> tuple[dict, dict]:
        status = Status(folder / "status.json")
        result = layers.run(folder / "v.mp4", folder, app_root=folder, model_root=folder, ffmpeg=Path("ffmpeg"), language="zh",
                            status=status, transcribe=transcript, **options)
        return result, json.loads((folder / layers.LAYERS_FILE).read_text(encoding="utf-8"))

    def test_offline_lines_make_two_layers_with_the_sensitive_words_applied(self):
        policy = SensitivePolicy.from_mapping({"mode": "variant", "words": [{"from": "chết", "to": "chớt"}]})
        with TemporaryDirectory() as temp:
            result, document = self.run_layers(Path(temp), offline=offline, settings=lambda *_: Settings(sensitive=policy))
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual([line["text"] for line in document["source"]], ["他死了", "我们走吧"])
        self.assertEqual([line["text"] for line in document["vi"]], ["Anh ta chớt rồi", "Chúng ta đi thôi"])
        self.assertEqual(document["vi"][1]["start_ms"], 1600)
        self.assertIsNone(document["ai"])

    def test_the_ai_pass_replaces_lines_and_a_failing_ai_keeps_the_offline_ones(self):
        def better(cues, route, **options):
            options["on_progress"](1, 1)
            return Result((replace(cues[0], translated_text="Hắn chết rồi"), cues[1]), 1)
        with TemporaryDirectory() as temp:
            result, document = self.run_layers(Path(temp), offline=offline, in_context=better, settings=lambda *_: Settings(route=Route()))
            self.assertEqual(document["vi"][0]["text"], "Hắn chết rồi")
            self.assertIn("Gemini", result["message"])

        def broken(*_a, **_k):
            raise OSError("network")
        with TemporaryDirectory() as temp:
            _, document = self.run_layers(Path(temp), offline=offline, in_context=broken, settings=lambda *_: Settings(route=Route()))
            self.assertEqual(document["vi"][0]["text"], "Anh ta chết rồi")
            self.assertTrue(any(warning.startswith("AI_TRANSLATION_FAILED") for warning in document["warnings"]))

    def test_no_translation_at_all_is_a_readable_failure_and_cancel_stops(self):
        def none(*_a, **_k):
            raise RuntimeError("no model")
        with TemporaryDirectory() as temp:
            with self.assertRaises(layers.LayersError):
                self.run_layers(Path(temp), offline=none, settings=lambda *_: Settings())
        with TemporaryDirectory() as temp:
            (Path(temp) / "cancel").write_text("1")
            with self.assertRaises(layers.Cancelled):
                self.run_layers(Path(temp), offline=offline, settings=lambda *_: Settings())

    def test_main_writes_a_failed_status(self):
        with TemporaryDirectory() as temp:
            code = layers.main(["--video", str(Path(temp) / "missing.mp4"), "--out-dir", temp, "--app-root", temp, "--model-root", temp,
                                "--ffmpeg", "ffmpeg", "--language", "zh"])
            status = json.loads((Path(temp) / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(status["state"], "FAILED")
        self.assertIn("Không tách được phụ đề", status["message"])


if __name__ == "__main__":
    unittest.main()

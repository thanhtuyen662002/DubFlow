"""Phim tài liệu with a Vibi.pro voice and the Mega Projects mix (#632)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from engine.dubflow.docu import overlays, roughcut
from engine.dubflow.docu import __main__ as docu_main
from engine.dubflow.docu.project import script_path, write_json
from engine.dubflow.tts import vibi

from test_docu import make_run
from test_narration import script as narration_script

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tts"))
from test_vibi import FakeVibi, SETTINGS, tone_wav  # noqa: E402

RealClient = vibi.VibiClient
FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")


class MegaMixTests(unittest.TestCase):
    def test_mix_option_is_saved_and_described(self):
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            self.assertEqual(overlays.load_options(run)["mix"], "standard")
            self.assertEqual(overlays.save_options(run, mix="mega")["mix"], "mega")
            self.assertEqual(overlays.describe(overlays.load_options(run))["mix"], "mega")
            with self.assertRaises(Exception):
                overlays.save_options(run, mix="loud")

    def test_mega_compresses_the_voice_lowers_the_bed_and_caps_the_effects(self):
        graph = overlays.ducked_mix(bed=True, ambience=3, mega=True)
        self.assertTrue(graph.startswith(f"[1:a]{overlays.VOICE_COMPRESSOR},apad,asplit=2[voice][key]"))
        self.assertIn("[3:a]volume=6dB,alimiter=limit=0.178:level=disabled[room]", graph)
        self.assertEqual(overlays.ducked_mix(bed=True, ambience=3), overlays.ducked_mix(bed=True, ambience=3, mega=False))
        self.assertIn("loudnorm=I=-24:", " ".join(overlays.bed_argv(Path("ffmpeg"), [("epic_build", 0.0, 10.0)], 10.0, Path("bed.wav"),
                                                                    overlays.MEGA_BED_LUFS)))
        self.assertTrue(-25 <= overlays.MEGA_BED_LUFS <= -22)
        argv = roughcut.mux_argv(Path("ffmpeg"), "concat.txt", Path("n.wav"), 10.0, Path("o.mp4"), mega=True)
        self.assertIn(f"[1:a]{overlays.VOICE_COMPRESSOR},apad[audio]", argv)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class VibiNarrationTests(unittest.TestCase):
    def test_narrate_with_a_vibi_voice_pays_each_sentence_once(self):
        fake = FakeVibi(audio=tone_wav(0.6), polls=1)

        def make_client(key, **_kwargs):
            self.assertEqual(key, "sk_test_1234567890")
            return RealClient(key, opener=fake, sleep=lambda _s: None)
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            write_json(script_path(run), narration_script())
            settings = Path(temp) / "vibi.json"
            settings.write_text(json.dumps(SETTINGS), encoding="utf-8")
            argv = ["narrate", "--run-dir", str(run), "--app-root", temp, "--model-root", temp, "--ffmpeg", FFMPEG, "--voice", "vibi",
                    "--vibi", str(settings)]
            with mock.patch.dict(os.environ, {vibi.KEY_ENV: "sk_test_1234567890"}), \
                    mock.patch.object(vibi, "VibiClient", side_effect=make_client), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                docu_main.main(argv)
            result = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertNotIn(vibi.KEY_ENV, os.environ)
            timeline = json.loads((run / "master_audio_timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["voice_id"], "Arnold (ElevenLabs)")
            self.assertTrue(timeline["producer"].startswith("vibi:"))
            spoken = fake.tasks
            self.assertGreater(spoken, 0)
            posts = [entry for entry in fake.requests if entry[0] == "POST"]
            self.assertEqual(posts[0][3]["voice_settings"]["stability"], 0.45)
            with mock.patch.dict(os.environ, {vibi.KEY_ENV: "sk_test_1234567890"}), \
                    mock.patch.object(vibi, "VibiClient", side_effect=make_client), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                docu_main.main(argv)
            self.assertEqual(fake.tasks, spoken)  # Every sentence came from the cache.

    def test_out_of_credit_is_a_plain_failure(self):
        fake = FakeVibi(fail_status=402)
        with TemporaryDirectory() as temp:
            run = make_run(Path(temp))
            write_json(script_path(run), narration_script())
            settings = Path(temp) / "vibi.json"
            settings.write_text(json.dumps(SETTINGS), encoding="utf-8")
            with mock.patch.dict(os.environ, {vibi.KEY_ENV: "sk_test_1234567890"}), \
                    mock.patch.object(vibi, "VibiClient", side_effect=lambda key, **_k: RealClient(key, opener=fake, sleep=lambda _s: None)), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                docu_main.main(["narrate", "--run-dir", str(run), "--app-root", temp, "--model-root", temp, "--ffmpeg", FFMPEG,
                                "--voice", "vibi", "--vibi", str(settings)])
            result = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(result["state"], "FAILED")
            self.assertIn("hết credit", result["message"])


if __name__ == "__main__":
    unittest.main()

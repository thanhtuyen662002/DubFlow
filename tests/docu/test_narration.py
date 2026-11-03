from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import wave

from engine.dubflow.docu import narration, units
from engine.dubflow.docu.project import DocuError
from engine.dubflow.tts import kokoro

FFMPEG = os.environ.get("DUBFLOW_TEST_FFMPEG") or shutil.which("ffmpeg")
ROOT = Path(__file__).resolve().parents[2]
RATE = 24_000


class FakeVoice:
    """Speaks every character as 10 ms of a quiet tone, so times are predictable."""

    sample_rate = RATE

    def __init__(self):
        self.spoken: list[str] = []
        self.closed = False

    def speak(self, text):
        self.spoken.append(text)
        count = len(text) * RATE // 100
        return [0.2 * math.sin(i / 7) for i in range(count)]

    def close(self):
        self.closed = True


def script() -> dict:
    return {"chapters": [
        {"chapter_type": "HOOK", "scenes": [
            {"scene_id": "sc_001", "narration_text": "One two. Three four!"},
            {"scene_id": "sc_002", "narration_text": "   "},
            {"scene_id": "sc_003", "narration_text": "The U.S. Army came. Dr. Lee too."}]},
        {"chapter_type": "PROBLEM", "scenes": [{"scene_id": "sc_004", "narration_text": "Five."}]},
        {"chapter_type": "EPILOGUE", "scenes": [{"scene_id": "sc_009", "narration_text": "Ignored."}]},
    ]}


class SentenceTests(unittest.TestCase):
    def test_sentences_keep_abbreviations_and_quotes(self):
        self.assertEqual(narration.sentences("It cost $4.8 billion. The U.S. Army agreed! “Why?” Dr. Lee asked. 2021 was late."),
                         ["It cost $4.8 billion.", "The U.S. Army agreed!", "“Why?”", "Dr. Lee asked.", "2021 was late."])
        self.assertEqual(narration.sentences("  "), [])

    def test_long_sentences_split_at_clauses(self):
        long = ", ".join(f"clause number {i} with several words in it" for i in range(12)) + "."
        pieces = narration._pieces_of(long)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(piece) <= narration.MAX_PIECE for piece in pieces))
        self.assertEqual(" ".join(pieces), long)

    def test_plan_pauses(self):
        pieces = narration.plan(script())
        self.assertEqual([(p.scene_id, p.text, p.pause) for p in pieces], [
            ("sc_001", "One two.", narration.SENTENCE_PAUSE), ("sc_001", "Three four!", narration.SCENE_PAUSE),
            ("sc_003", "The U.S. Army came.", narration.SENTENCE_PAUSE), ("sc_003", "Dr. Lee too.", narration.CHAPTER_PAUSE),
            ("sc_004", "Five.", 0.0)])
        with self.assertRaises(DocuError):
            narration.plan({"chapters": [{"chapter_type": "HOOK", "scenes": [{"scene_id": "sc_001", "narration_text": " "}]}]})

    def test_script_hash_ignores_flags(self):
        value = script()
        flagged = json.loads(json.dumps(value))
        flagged["chapters"][0]["scenes"][0]["needs_review"] = True
        self.assertEqual(narration.script_sha256(value), narration.script_sha256(flagged))
        flagged["chapters"][0]["scenes"][0]["narration_text"] = "Changed."
        self.assertNotEqual(narration.script_sha256(value), narration.script_sha256(flagged))

    def test_captions_are_balanced_two_line_cues(self):
        text = "Hidden deep within a seismic fault line, engineers poured 280 million cubic feet of concrete."
        srt = narration.captions([{"sentences": [{"text": text, "start": 1.0, "end": 7.0}]}])
        cues = [block.split("\n") for block in srt.strip().split("\n\n")]
        self.assertEqual(len(cues), 2)
        self.assertTrue(all(len(line) <= 50 for cue in cues for line in cue[2:]))
        self.assertTrue(cues[0][1].startswith("00:00:01,000 --> ") and cues[1][1].endswith("--> 00:00:07,000"))
        self.assertEqual(" ".join(" ".join(cue[2:]) for cue in cues), text)

    def test_inch_is_a_length(self):
        self.assertEqual([(q.dimension, round(q.value, 4)) for q in units.extract("a 3-inch bolt")], [("length", 0.0762)])


class JoinTests(unittest.TestCase):
    def test_join_times_come_from_samples(self):
        with TemporaryDirectory() as temp:
            work = Path(temp)
            pieces = narration.plan(script())
            voice = FakeVoice()
            paths = []
            for index, piece in enumerate(pieces):
                path = work / f"{index}.wav"
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(RATE)
                    handle.writeframes(narration._pcm(voice.speak(piece.text)))
                paths.append(path)
            scenes, chapters, position = narration._join(pieces, paths, work / "out.wav")
            first = len("One two.") / 100
            self.assertEqual(scenes[0]["sentences"][0], {"text": "One two.", "start": 0.0, "end": round(first, 3)})
            self.assertEqual(scenes[0]["sentences"][1]["start"], round(first + narration.SENTENCE_PAUSE, 3))
            self.assertEqual(scenes[1]["start"], round(scenes[0]["end"] + narration.SCENE_PAUSE, 3))
            self.assertEqual(scenes[0]["slot_end"], scenes[1]["start"])
            self.assertEqual(chapters[1]["start"], round(scenes[1]["end"] + narration.CHAPTER_PAUSE, 3))
            self.assertEqual([c["chapter_type"] for c in chapters], ["HOOK", "PROBLEM"])
            self.assertEqual(scenes[-1]["slot_end"], round(position / RATE, 3))
            self.assertEqual(scenes[0]["exact_duration_ms"], round((scenes[0]["end"] - scenes[0]["start"]) * 1000))
            with wave.open(str(work / "out.wav"), "rb") as handle:
                self.assertEqual(handle.getnframes(), position)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class RunTests(unittest.TestCase):
    def test_run_caches_normalises_and_cancels(self):
        with TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            run_dir.mkdir()
            voices = []

            def speaker(_report):
                voices.append(FakeVoice())
                return voices[-1]

            result = narration.run(run_dir, script(), speaker=speaker, voice_id="am_michael", speed=1.0, ffmpeg=Path(FFMPEG))
            self.assertEqual(result["state"], "COMPLETED", result)
            self.assertTrue(voices[0].closed)
            timeline = json.loads((run_dir / "master_audio_timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["script_sha256"], narration.script_sha256(script()))
            self.assertEqual([s["scene_id"] for s in timeline["scenes"]], ["sc_001", "sc_003", "sc_004"])
            with wave.open(str(run_dir / "narration.wav"), "rb") as handle:
                self.assertEqual(handle.getframerate(), narration.OUTPUT_RATE)
                self.assertAlmostEqual(handle.getnframes() / narration.OUTPUT_RATE, timeline["duration"], delta=0.01)
            self.assertIn("-->", (run_dir / "captions_en.srt").read_text(encoding="utf-8"))
            loud = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(run_dir / "narration.wav"), "-af", "ebur128=peak=true", "-f", "null", "-"],
                                  capture_output=True, text=True).stderr
            integrated = float(loud.rsplit(" I:", 1)[1].split("LUFS")[0])
            self.assertAlmostEqual(integrated, narration.TARGET_LUFS, delta=1.0)
            # A second run speaks nothing new: every sentence is cached.
            again = narration.run(run_dir, script(), speaker=speaker, voice_id="am_michael", speed=1.0, ffmpeg=Path(FFMPEG))
            self.assertEqual(again["state"], "COMPLETED")
            self.assertEqual(len(voices), 1)
            # Another voice is new speech; a cancel stops it before the first sentence.
            (run_dir / ".dubflow-work" / "narration").mkdir(parents=True, exist_ok=True)

            def cancelling(_report):
                (run_dir / ".dubflow-work" / "narration" / "cancel").write_bytes(b"")
                return FakeVoice()
            stopped = narration.run(run_dir, script(), speaker=cancelling, voice_id="bf_emma", speed=1.0, ffmpeg=Path(FFMPEG))
            self.assertEqual(stopped["state"], "CANCELLED")


class KokoroProfileTests(unittest.TestCase):
    def test_manifest_voices_and_pack(self):
        profile = kokoro.load_profile(ROOT)
        self.assertEqual(profile.default_voice_id, "am_michael")
        self.assertEqual(profile.voice("bm_george").lexicon, "lexicon-gb-en.txt")
        self.assertEqual(profile.voice("af_heart").speaker_id, 3)
        value = json.loads((ROOT / kokoro.PROFILE_PATH).read_text(encoding="utf-8"))
        paths = {artifact["path"].split(profile.pack + "/", 1)[1] for artifact in value["artifacts"]}
        self.assertTrue({"model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-gb-en.txt",
                         "espeak-ng-data/phondata", "espeak-ng-data/en_dict"} <= paths)
        self.assertTrue(all(a["url"].startswith("https://huggingface.co/csukuangfj/kokoro-multi-lang-v1_0/resolve/") for a in value["artifacts"]))
        with self.assertRaises(kokoro.KokoroError):
            profile.voice("zz_nobody")

    def test_speaker_uses_the_voice_settings(self):
        calls = []

        class Engine:
            def generate(self, text, sid, speed):
                calls.append((text, sid, speed))
                return type("Audio", (), {"sample_rate": 24000, "samples": [0.1, 0.2]})()

        def factory(paths):
            calls.append(paths["lexicon"].name)
            return Engine()
        speaker = kokoro.KokoroSpeaker(ROOT, Path("models"), "bm_george", speed=0.9, engine_factory=factory)
        self.assertEqual(speaker.speak("Hello."), [0.1, 0.2])
        self.assertEqual(calls, ["lexicon-gb-en.txt", ("Hello.", 26, 0.9)])
        with self.assertRaises(kokoro.KokoroError):
            kokoro.KokoroSpeaker(ROOT, Path("models"), "bm_george", speed=1.5, engine_factory=factory)


if __name__ == "__main__":
    unittest.main()

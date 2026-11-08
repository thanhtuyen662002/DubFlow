"""Vibi.pro voices (#632) against a mocked API: the cloud CI cannot reach vibi.pro."""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import wave

from engine.dubflow.tts import vibi


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeVibi:
    """Answers like api.vibi.pro; records every request."""

    def __init__(self, audio: bytes = b"ID3" + b"\x00" * 200, polls: int = 2, fail_status: int | None = None) -> None:
        self.audio = audio
        self.polls = polls
        self.fail_status = fail_status
        self.requests: list[tuple[str, str, dict, dict | None]] = []
        self.tasks = 0

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), url.path, query, body, dict(request.header_items())))
        if self.fail_status:
            raise urllib.error.HTTPError(request.full_url, self.fail_status, "x", {}, io.BytesIO(b'{"error":"nope"}'))
        path = url.path
        if path == "/v1/auth/me":
            return FakeResponse(json.dumps({"id": "u", "email": "a@b.c", "name": "Oscar", "credit_balance": 1200}).encode())
        if path == "/v1/models":
            if query.get("provider") == "minimax":
                return FakeResponse(json.dumps([{"model_id": "speech-2.8-hd", "name": "Speech 2.8 HD", "credit_ratio": 1}]).encode())
            return FakeResponse(json.dumps([
                {"model_id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5", "can_do_text_to_speech": True, "can_use_style": True,
                 "can_use_speaker_boost": True, "languages": [{"language_id": "en", "name": "English"}]},
                {"model_id": "eleven_english_sts_v2", "name": "STS", "can_do_text_to_speech": False}]).encode())
        if path == "/v1/shared-voices":
            return FakeResponse(json.dumps({"voices": [
                {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold", "gender": "male", "age": "middle_aged", "accent": "american",
                 "language": "en", "descriptive": "deep", "use_case": "narrative_story", "description": "A documentary voice",
                 "preview_url": "https://storage.example/arnold.mp3", "cloned_by_count": 9,
                 "verified_languages": [{"language": "en"}, {"language": "vi"}]},
                {"voice_id": "x", "name": "Bad preview", "preview_url": "http://insecure/x.mp3"}], "has_more": True}).encode())
        if path == "/v1/default-voices":
            return FakeResponse(json.dumps({"voices": [{"voice_id": "a1", "name": "Rachel", "gender": "female", "accent": "american"},
                                                       {"voice_id": "a2", "name": "Adam", "gender": "male", "accent": "american"}]}).encode())
        if path == "/v1/minimax/system-voices":
            return FakeResponse(json.dumps({"voice_list": [{"voice_id": "226905123659939", "voice_name": "Deep Man", "uniq_id": "English_ManWithDeepVoice",
                                                            "tag_list": ["English", "Male", "Narration"], "sample_audio": "https://cdn.example/s.mp3"}],
                                            "total": 300, "has_more": True}).encode())
        if path.startswith("/v1/text-to-speech/"):
            self.tasks += 1
            return FakeResponse(json.dumps({"id": f"task-{self.tasks}", "status": "pending"}).encode())
        if path.startswith("/v1/history/"):
            task = path.rsplit("/", 1)[1]
            self.polls -= 1
            if self.polls > 0:
                return FakeResponse(json.dumps({"id": task, "status": "processing", "progress": 50}).encode())
            return FakeResponse(json.dumps({"id": task, "status": "completed", "result": {"audio_url": f"https://api.vibi.pro/audio/{task}.mp3"}}).encode())
        if path.startswith("/audio/") or path.endswith(".mp3"):
            return FakeResponse(self.audio)
        raise AssertionError(f"unexpected {path}")


def client(fake: FakeVibi) -> vibi.VibiClient:
    return vibi.VibiClient("sk_test_1234567890", opener=fake, sleep=lambda _s: None)


SETTINGS = {"provider": "elevenlabs", "voice_id": "VR6AewLTigWG4xSOukaG", "voice_name": "Arnold", "model_id": "eleven_turbo_v2_5",
            "language_code": "en", "stability": 0.45, "similarity_boost": 0.78, "style": 0.15, "use_speaker_boost": True, "speed": 1.0}


def tone_wav(seconds: float = 1.0, rate: int = 22050) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))))
    return out.getvalue()


class SettingsTests(unittest.TestCase):
    def test_mega_projects_preset_is_oscars_recipe(self):
        self.assertEqual(vibi.MEGA_PROJECTS["model_id"], "eleven_turbo_v2_5")
        self.assertTrue(0.40 <= vibi.MEGA_PROJECTS["stability"] <= 0.50)
        self.assertTrue(0.75 <= vibi.MEGA_PROJECTS["similarity_boost"] <= 0.80)
        self.assertTrue(0.10 <= vibi.MEGA_PROJECTS["style"] <= 0.20)

    def test_elevenlabs_request_carries_every_slider(self):
        settings = vibi.VoiceSettings.from_mapping(SETTINGS)
        self.assertEqual(settings.request("Hi"), {"text": "Hi", "model_id": "eleven_turbo_v2_5", "provider": "elevenlabs", "language_code": "en",
                                                  "voice_settings": {"stability": 0.45, "similarity_boost": 0.78, "style": 0.15,
                                                                     "use_speaker_boost": True, "speed": 1.0}})
        self.assertEqual(settings.label(), "Arnold (ElevenLabs)")

    def test_minimax_request_uses_speed_pitch_and_volume(self):
        settings = vibi.VoiceSettings.from_mapping({"provider": "minimax", "voice_id": "English_ManWithDeepVoice", "model_id": "speech-2.8-hd",
                                                    "language_code": "English", "speed": 1.5, "pitch": -3, "vol": 2})
        self.assertEqual(settings.request("Hi")["voice_settings"], {"speed": 1.5, "pitch": -3, "vol": 2.0})

    def test_identity_follows_the_sound_not_the_name(self):
        base = vibi.VoiceSettings.from_mapping(SETTINGS)
        self.assertEqual(base.identity(), vibi.VoiceSettings.from_mapping({**SETTINGS, "voice_name": "Other"}).identity())
        self.assertNotEqual(base.identity(), vibi.VoiceSettings.from_mapping({**SETTINGS, "stability": 0.5}).identity())

    def test_out_of_range_values_are_refused(self):
        for change in ({"stability": 1.5}, {"style": -0.1}, {"speed": 1.5}, {"provider": "openai"}, {"voice_id": "../x"},
                       {"use_speaker_boost": "yes"}, {"model_id": "a b;rm"}):
            with self.subTest(change=change), self.assertRaises(vibi.VibiError):
                vibi.VoiceSettings.from_mapping({**SETTINGS, **change})
        with self.assertRaises(vibi.VibiError):
            vibi.VoiceSettings.from_mapping({**SETTINGS, "provider": "minimax", "pitch": 13})


class ClientTests(unittest.TestCase):
    def test_key_is_sent_as_xi_api_key_and_balance_is_read(self):
        fake = FakeVibi()
        self.assertEqual(client(fake).me()["credit_balance"], 1200)
        method, path, _query, _body, headers = fake.requests[0]
        self.assertEqual((method, path), ("GET", "/v1/auth/me"))
        self.assertEqual(headers.get("Xi-api-key"), "sk_test_1234567890")

    def test_missing_key_says_where_to_put_it(self):
        with self.assertRaises(vibi.VibiError) as caught:
            vibi.VibiClient("  ")
        self.assertEqual(caught.exception.code, "KEY_MISSING")
        self.assertIn("Cài đặt", caught.exception.message)

    def test_refused_key_and_empty_balance_are_plain_messages(self):
        for status, code in ((401, "KEY_INVALID"), (402, "NO_CREDITS"), (400, "REQUEST")):
            with self.subTest(status=status), self.assertRaises(vibi.VibiError) as caught:
                client(FakeVibi(fail_status=status)).me()
            self.assertEqual(caught.exception.code, code)

    def test_server_errors_are_retried_a_bounded_number_of_times(self):
        fake = FakeVibi(fail_status=503)
        with self.assertRaises(vibi.VibiError) as caught:
            client(fake).me()
        self.assertEqual(caught.exception.code, "SERVER")
        self.assertEqual(len(fake.requests), vibi.RETRIES)

    def test_models_skip_speech_to_speech_and_fall_back_when_empty(self):
        fake = FakeVibi()
        models = client(fake).models("elevenlabs")
        self.assertEqual([model["model_id"] for model in models], ["eleven_turbo_v2_5"])
        self.assertEqual(client(fake).models("minimax")[0]["model_id"], "speech-2.8-hd")
        self.assertEqual(fake.requests[-1][2], {"provider": "minimax"})

    def test_shared_voices_are_filtered_paged_and_normalised(self):
        fake = FakeVibi()
        page = client(fake).voices("elevenlabs", "shared", page=2, search="  deep  voice ", gender="Male", language="en",
                                   use_case="narrative_and_story")
        _method, path, query, _body, _headers = fake.requests[0]
        self.assertEqual(path, "/v1/shared-voices")
        self.assertEqual(query, {"page_size": "30", "page": "2", "search": "deep voice", "gender": "male", "required_languages": "en",
                                 "use_cases": "narrative_and_story", "sort": "trending"})
        self.assertTrue(page["has_more"])
        arnold = page["voices"][0]
        self.assertEqual((arnold["voice_id"], arnold["name"], arnold["accent"], arnold["age"]), ("VR6AewLTigWG4xSOukaG", "Arnold", "american", "middle aged"))
        self.assertEqual(arnold["languages"], ["en", "vi"])
        self.assertEqual(page["voices"][1]["preview_url"], "")  # Not HTTPS: never played.

    def test_default_voices_filter_by_gender_here(self):
        page = client(FakeVibi()).voices("elevenlabs", "default", gender="female")
        self.assertEqual([voice["name"] for voice in page["voices"]], ["Rachel"])

    def test_minimax_pages_count_from_one_and_use_the_readable_id(self):
        fake = FakeVibi()
        page = client(fake).voices("minimax", "system", page=0, gender="male", language="English")
        self.assertEqual(fake.requests[0][2], {"page": "1", "page_size": "30", "gender": "Male", "language": "English"})
        voice = page["voices"][0]
        self.assertEqual((voice["voice_id"], voice["gender"], voice["tags"], page["total"]), ("English_ManWithDeepVoice", "Male", ["English", "Narration"], 300))

    def test_speech_polls_the_task_then_downloads_with_the_key_only_from_vibi(self):
        fake = FakeVibi(polls=3)
        settings = vibi.VoiceSettings.from_mapping(SETTINGS)
        audio = client(fake).speak(settings, "  It swallowed a staggering — twenty billion dollars. ")
        self.assertEqual(audio, fake.audio)
        posts = [entry for entry in fake.requests if entry[0] == "POST"]
        self.assertEqual(posts[0][1], "/v1/text-to-speech/VR6AewLTigWG4xSOukaG")
        self.assertEqual(posts[0][3]["text"], "It swallowed a staggering — twenty billion dollars.")
        self.assertEqual([entry[1] for entry in fake.requests].count("/v1/history/task-1"), 3)
        with self.assertRaises(vibi.VibiError):
            client(fake).download("http://api.vibi.pro/audio/x.mp3")
        other = client(fake)
        other.download("https://cdn.example/preview.mp3")
        self.assertNotIn("Xi-api-key", fake.requests[-1][4])

    def test_failed_task_reports_the_reason(self):
        fake = FakeVibi()

        def failing(request, timeout=None):
            if "/v1/history/" in request.full_url:
                return FakeResponse(b'{"status":"failed","error":"voice not available"}')
            return fake(request, timeout)
        with self.assertRaises(vibi.VibiError) as caught:
            vibi.VibiClient("sk_test_1234567890", opener=failing, sleep=lambda _s: None).speak(vibi.VoiceSettings.from_mapping(SETTINGS), "Hi")
        self.assertIn("voice not available", caught.exception.message)


class CacheTests(unittest.TestCase):
    def test_a_sentence_is_paid_for_once(self):
        fake = FakeVibi()
        settings = vibi.VoiceSettings.from_mapping(SETTINGS)
        with tempfile.TemporaryDirectory() as temp:
            cache = vibi.AudioCache(temp)
            first = cache.fetch(client(fake), settings, "Hello there.")
            second = cache.fetch(client(fake), settings, "Hello there.")
            self.assertEqual(first, second)
            self.assertEqual(fake.tasks, 1)
            cache.fetch(client(fake), vibi.VoiceSettings.from_mapping({**SETTINGS, "style": 0.2}), "Hello there.")
            self.assertEqual(fake.tasks, 2)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs FFmpeg")
class AudioTests(unittest.TestCase):
    def test_speaker_returns_48k_samples_and_atempo_shortens(self):
        ffmpeg = shutil.which("ffmpeg")
        fake = FakeVibi(audio=tone_wav(1.0))
        settings = vibi.VoiceSettings.from_mapping(SETTINGS)
        with tempfile.TemporaryDirectory() as temp:
            speaker = vibi.VibiSpeaker(settings, "", ffmpeg, cache_root=temp, client=client(fake))
            samples = speaker.speak("Hello.")
            self.assertEqual(speaker.sample_rate, 48_000)
            self.assertAlmostEqual(len(samples) / 48_000, 1.0, delta=0.05)
            self.assertGreater(max(abs(value) for value in samples), 0.1)
            faster = vibi.decode(ffmpeg, fake.audio, tempo=1.25)
            self.assertAlmostEqual(len(faster) / 48_000, 0.8, delta=0.05)

    def test_dubbing_engine_drives_vieneu_duration_fit_with_vibi_speech(self):
        from engine.dubflow.tts.adapter import FAST_FIT_MAX_MILLI
        ffmpeg = shutil.which("ffmpeg")
        fake = FakeVibi(audio=tone_wav(1.2))
        settings = vibi.VoiceSettings.from_mapping({**SETTINGS, "language_code": "vi"})
        with tempfile.TemporaryDirectory() as temp:
            pack, voice, engine = vibi.dubbing_engine(settings, "", ffmpeg, cache_root=temp, client=client(fake))
            self.assertTrue(voice.voice_id.startswith("vibi-"))
            self.assertEqual(voice.language, "vi")
            self.assertTrue(engine.healthcheck(voice).ready)
            natural = engine._tts.generate("Xin chào", sid=0, speed=1.0)
            fitted = engine._tts.generate("Xin chào", sid=0, speed=1.2)
            self.assertLess(len(fitted.samples), len(natural.samples))
            self.assertEqual(fake.tasks, 1)  # The tempo pass reuses the same speech.
            self.assertGreaterEqual(FAST_FIT_MAX_MILLI, 1200)
            engine.close()


class CommandTests(unittest.TestCase):
    def test_presets_need_no_key(self):
        result = vibi.handle({"action": "presets"}, "")
        self.assertEqual(result["presets"]["mega_projects"], vibi.MEGA_PROJECTS)
        self.assertEqual(result["key_page"], "https://vibi.pro/docs")

    def test_voices_and_preview_actions(self):
        fake = FakeVibi()
        result = vibi.handle({"action": "voices", "provider": "minimax", "source": "system", "page": 0, "filters": {"search": "deep"}}, "k",
                             client=client(fake))
        self.assertEqual(result["voices"][0]["name"], "Deep Man")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "p.mp3"
            vibi.handle({"action": "preview", "url": "https://cdn.example/s.mp3", "out": str(out)}, "k", client=client(fake))
            self.assertEqual(out.read_bytes(), fake.audio)
            with self.assertRaises(vibi.VibiError):
                vibi.handle({"action": "preview", "url": "file:///etc/passwd", "out": str(out)}, "k", client=client(fake))
            with self.assertRaises(vibi.VibiError):
                vibi.handle({"action": "preview", "url": "https://cdn.example/s.mp3", "out": "relative.mp3"}, "k", client=client(fake))

    def test_command_line_reads_the_key_from_the_environment_and_answers_one_json_line(self):
        env = {key: value for key, value in os.environ.items() if key != vibi.KEY_ENV}
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([os.sys.executable, "-B", "-m", "engine.dubflow.tts.vibi"], input=b'{"action":"me"}', capture_output=True,
                                cwd=root, env=env, timeout=60)
        answer = json.loads(result.stdout.decode().strip().splitlines()[-1])
        self.assertEqual((answer["ok"], answer["code"]), (False, "KEY_MISSING"))


if __name__ == "__main__":
    unittest.main()

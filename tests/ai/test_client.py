import io
import json
import os
import unittest
from urllib.error import URLError

from engine.dubflow.ai import __main__ as cli
from engine.dubflow.ai.client import PRESETS, AiError, ask, ask_json, check_base_url, list_models, make_route, parse_json_text, pick_model

KEY = "sk-test-0123456789abcdef"
SCHEMA = {"type": "object", "properties": {"zh": {"type": "string"}}, "required": ["zh"], "additionalProperties": False}


class Fake:
    """Answers queued (status, body) pairs and records each request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer[0], answer[1]
        headers = answer[2] if len(answer) > 2 else {}
        return status, headers, json.dumps(body).encode() if not isinstance(body, bytes) else body

    def body(self, index=-1):
        return json.loads(self.requests[index].data.decode())


def chat(text, finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


class OpenAiShapedTest(unittest.TestCase):
    def test_every_openai_preset_posts_chat_completions_with_a_bearer_key(self):
        for provider, preset in PRESETS.items():
            if preset.dialect != "openai" or provider == "custom":
                continue
            with self.subTest(provider=provider):
                fake = Fake((200, chat('{"zh": "养猪"}')))
                self.assertEqual(ask_json(make_route(provider, KEY), "sys", SCHEMA, [{"text": "nuôi lợn"}], transport=fake), {"zh": "养猪"})
                request = fake.requests[0]
                self.assertEqual(request.full_url, preset.base_url + "/chat/completions")
                self.assertEqual(request.get_header("Authorization"), f"Bearer {KEY}")
                self.assertNotIn(KEY, request.full_url)
                body = fake.body()
                self.assertEqual(body["model"], preset.model)
                self.assertEqual(body["messages"][0]["role"], "system")
                self.assertIn('"zh"', body["messages"][0]["content"])  # the schema travels in the prompt
                self.assertEqual(body["response_format"], {"type": "json_object"})
                self.assertIn("max_completion_tokens" if provider == "openai" else "max_tokens", body)

    def test_custom_server_needs_a_base_url_and_skips_json_mode(self):
        with self.assertRaises(AiError):
            make_route("custom", KEY, model="m")
        fake = Fake((200, chat("```json\n{\"zh\": \"好\"}\n```")))
        route = make_route("custom", KEY, model="qwen2.5:14b", base_url="https://llm.example.com/v1/")
        self.assertEqual(ask_json(route, "s", SCHEMA, [{"text": "t"}], transport=fake), {"zh": "好"})
        self.assertEqual(fake.requests[0].full_url, "https://llm.example.com/v1/chat/completions")
        self.assertNotIn("response_format", fake.body())

    def test_local_server_may_use_http_and_no_key(self):
        route = make_route("custom", "", model="llama3", base_url="http://localhost:11434/v1")
        fake = Fake((200, chat('{"zh": "x"}')))
        ask_json(route, "s", SCHEMA, [{"text": "t"}], transport=fake)
        self.assertIsNone(fake.requests[0].get_header("Authorization"))
        for bad in ("http://example.com/v1", "ftp://x", "https://user:pw@x.com", "https://x.com/v1?key=1"):
            with self.subTest(bad=bad), self.assertRaises(AiError):
                check_base_url(bad)

    def test_presets_ignore_a_foreign_base_url(self):
        self.assertEqual(make_route("openai", KEY, base_url="https://evil.example.com").base_url, PRESETS["openai"].base_url)
        self.assertEqual(make_route("qwen", KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1").base_url,
                         "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_a_server_rejecting_response_format_is_asked_without_it(self):
        fake = Fake((400, {"error": {"message": "response_format is not supported"}}), (200, chat('{"zh": "a"}')))
        self.assertEqual(ask_json(make_route("groq", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake), {"zh": "a"})
        self.assertIn("response_format", fake.body(0))
        self.assertNotIn("response_format", fake.body(1))

    def test_images_go_as_data_urls_and_text_only_models_refuse_them(self):
        parts = [{"text": "mô tả"}, {"image": {"mediaType": "image/jpeg", "data": "QUJD"}}]
        fake = Fake((200, chat('{"zh": "a"}')))
        ask_json(make_route("openai", KEY), "s", SCHEMA, parts, transport=fake)
        self.assertEqual(fake.body()["messages"][1]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,QUJD")
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("groq", KEY), "s", SCHEMA, parts, transport=Fake())
        self.assertEqual(caught.exception.code, "INVALID")


class ErrorsAndRetriesTest(unittest.TestCase):
    def test_key_errors_are_not_retried_and_never_echo_the_key(self):
        fake = Fake((401, {"error": {"message": f"Incorrect API key provided: {KEY}"}}))
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("deepseek", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=lambda _: None)
        self.assertEqual(caught.exception.code, "KEY")
        self.assertNotIn(KEY, caught.exception.message)
        self.assertEqual(len(fake.requests), 1)

    def test_balance_errors_say_so(self):
        fake = Fake((402, {"error": {"message": "Insufficient Balance"}}))
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("deepseek", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake)
        self.assertEqual(caught.exception.code, "BALANCE")

    def test_rate_limits_and_server_errors_are_retried_twice_at_most(self):
        waits = []
        fake = Fake((429, {"error": {"message": "slow down"}}, {"Retry-After": "2"}), (503, {"error": {"message": "busy"}}), (200, chat('{"zh": "ok"}')))
        self.assertEqual(ask_json(make_route("openai", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=waits.append), {"zh": "ok"})
        self.assertEqual(waits, [2.0, 6.0])
        fake = Fake(*[(500, {"error": {"message": "x"}})] * 3)
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("openai", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=lambda _: None)
        self.assertEqual((caught.exception.code, len(fake.requests)), ("SERVER", 3))

    def test_a_long_requested_wait_stops_instead_of_hammering(self):
        fake = Fake((429, {"error": {"message": "quota"}}, {"retry-after": "3600"}))
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("openai", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=lambda _: self.fail("waited"))
        self.assertIn("phút", caught.exception.message)

    def test_network_failures_and_timeouts(self):
        with self.assertRaises(AiError) as caught:
            ask_json(make_route("openai", KEY), "s", SCHEMA, [{"text": "t"}], transport=Fake(URLError("no route")))
        self.assertEqual(caught.exception.code, "NETWORK")
        fake = Fake(TimeoutError(), TimeoutError(), (200, chat('{"zh": "a"}')))
        self.assertEqual(ask_json(make_route("openai", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=lambda _: None), {"zh": "a"})

    def test_a_retired_model_falls_back_to_the_providers_current_one(self):
        fake = Fake((404, {"error": {"message": "Model Not Exist"}}),
                    (200, {"data": [{"id": "deepseek-v9-pro"}, {"id": "deepseek-v9-flash"}, {"id": "deepseek-embed"}]}),
                    (200, chat('{"zh": "a"}')))
        value, used = ask(make_route("deepseek", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake)
        self.assertEqual((value, used.model), ({"zh": "a"}, "deepseek-v9-flash"))
        self.assertEqual(fake.requests[1].full_url, "https://api.deepseek.com/models")

    def test_format_errors_are_asked_once_more(self):
        fake = Fake((200, chat("xin lỗi, tôi không thể")), (200, chat('Đây: {"zh": "a"}')))
        self.assertEqual(ask_json(make_route("kimi", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake, sleep=lambda _: None), {"zh": "a"})


class OtherDialectsTest(unittest.TestCase):
    def test_gemini_uses_its_header_and_schema_subset(self):
        fake = Fake((200, {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"zh": "猪"}'}]}}]}))
        self.assertEqual(ask_json(make_route("gemini", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake), {"zh": "猪"})
        request = fake.requests[0]
        self.assertTrue(request.full_url.endswith("/models/gemini-3.5-flash-lite:generateContent"))
        self.assertEqual(request.get_header("X-goog-api-key"), KEY)
        schema = fake.body()["generationConfig"]["responseSchema"]
        self.assertEqual(schema["type"], "OBJECT")
        self.assertNotIn("additionalProperties", schema)

    def test_claude_uses_messages_with_its_headers(self):
        fake = Fake((200, {"content": [{"type": "text", "text": '{"zh": "猪"}'}], "stop_reason": "end_turn"}))
        self.assertEqual(ask_json(make_route("claude", KEY), "s", SCHEMA, [{"text": "t"}], transport=fake), {"zh": "猪"})
        self.assertEqual(fake.requests[0].full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(fake.requests[0].get_header("X-api-key"), KEY)

    def test_model_lists(self):
        fake = Fake((200, {"models": [{"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
                                      {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}]}))
        self.assertEqual(list_models(make_route("gemini", KEY), transport=fake), ["gemini-9-flash"])
        self.assertEqual(pick_model("openai", ["gpt-9-mini", "gpt-9", "gpt-9-realtime", "whisper-1"]), "gpt-9-mini")
        self.assertEqual(pick_model("grok", ["grok-9"]), "grok-9")
        self.assertIsNone(pick_model("custom", ["a"]))


class JsonTextTest(unittest.TestCase):
    def test_parses_fences_and_preambles(self):
        self.assertEqual(parse_json_text('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_text('Kết quả: {"a": 1} xong'), {"a": 1})
        with self.assertRaises(AiError):
            parse_json_text("không có json")


class CommandLineTest(unittest.TestCase):
    def test_ask_and_models_print_one_line_and_drop_the_key_from_the_environment(self):
        os.environ["DUBFLOW_AI_KEY"] = KEY
        out = io.StringIO()
        request = {"action": "ask", "provider": "openai", "system": "s", "schema": SCHEMA, "parts": [{"text": "t"}]}
        cli.main(io.StringIO(json.dumps(request)), out, Fake((200, chat('{"zh": "a"}'))))
        self.assertNotIn("DUBFLOW_AI_KEY", os.environ)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "value": {"zh": "a"}, "model": PRESETS["openai"].model})
        os.environ["DUBFLOW_AI_KEY"] = KEY
        out = io.StringIO()
        cli.main(io.StringIO(json.dumps({"action": "models", "provider": "openai"})), out, Fake((200, {"data": [{"id": "gpt-9-mini"}]})))
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "models": ["gpt-9-mini"], "suggested": "gpt-9-mini"})

    def test_failures_are_json_with_a_code(self):
        os.environ["DUBFLOW_AI_KEY"] = KEY
        out = io.StringIO()
        cli.main(io.StringIO(json.dumps({"action": "models", "provider": "openai"})), out, Fake((401, {"error": {"message": KEY}})))
        result = json.loads(out.getvalue())
        self.assertEqual((result["ok"], result["code"]), (False, "KEY"))
        self.assertNotIn(KEY, out.getvalue())
        out = io.StringIO()
        cli.main(io.StringIO("not json"), out)
        self.assertEqual(json.loads(out.getvalue())["code"], "INVALID")


if __name__ == "__main__":
    unittest.main()

"""The translation's AI pass over real HTTP to the $0 local stand-in (Issue #953): what it sends, and what comes back."""
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from engine.dubflow.ai.client import make_route
from engine.dubflow.translation import context_ai, glossary
from tests.ai import mock_openai_server as mock


@dataclass(frozen=True)
class Cue:
    cue_id: str
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None = None


class MockServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.log = Path(self.temp.name) / "mock-ai.jsonl"
        mock.Handler.log_path, mock.Handler.drop_terms = str(self.log), False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), mock.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.route = make_route("custom", "", model="mock", base_url=f"http://127.0.0.1:{self.server.server_address[1]}/v1")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def requests(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_the_ai_pass_sends_the_users_context_and_terms_and_keeps_the_mandatory_spelling(self):
        guide = glossary.resolve({"project": [glossary_term("拖拉机", "máy cày kéo", True), glossary_term("GPU", "card đồ họa")],
                                  "shared": [{"name": "Chung", "terms": [glossary_term("拖拉机", "xe kéo", True)]}],
                                  "context": {"topic": "Phim thử máy nông nghiệp", "people": "李明: nông dân"}})
        cues = [Cue("1", 0, 3000, "这台拖拉机很旧", "Máy kéo này cũ."), Cue("2", 3000, 6000, "我们走吧", "Đi thôi.")]
        result = context_ai.translate_in_context(cues, self.route, dubbing=True, guide=guide)
        self.assertEqual((result.windows_done, result.windows_total, result.cues_from_ai), (1, 1, 2))
        self.assertIn("máy cày kéo", result.cues[0].translated_text)
        sent = self.requests()
        self.assertEqual(len(sent), 2)  # the brief, then one window
        self.assertTrue(all(item["path"] == "/v1/chat/completions" and item["model"] == "mock" for item in sent))
        self.assertFalse(any(item["authorization"] for item in sent), "no key is sent to a local server without one")
        brief, window = sent
        self.assertIn("Chủ đề: Phim thử máy nông nghiệp", brief["user"])
        self.assertIn("Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: 拖拉机 → máy cày kéo", brief["user"])
        self.assertNotIn("xe kéo", brief["user"] + window["user"])  # the video's term wins over the shared one
        self.assertNotIn("GPU", brief["user"] + window["user"])  # a term no line says is not sent
        self.assertIn("拖拉机 → máy cày kéo", window["user"])
        self.assertIn("Nhân vật và cách xưng hô: 李明: nông dân", window["user"])

    def test_without_a_glossary_nothing_about_terms_is_sent(self):
        cues = [Cue("1", 0, 3000, "我们走吧", "Đi thôi.")]
        context_ai.translate_in_context(cues, self.route, dubbing=False)
        joined = "\n".join(item["user"] for item in self.requests())
        self.assertNotIn("Thuật ngữ BẮT BUỘC", joined)
        self.assertNotIn("Bối cảnh do người dùng ghi", joined)

    def test_answers(self):
        self.assertEqual(mock.answer("", "Lời thoại gốc:\n你好"), {"tom_tat": "Tóm tắt thử nghiệm.", "the_loai": "thử nghiệm", "nhan_vat": [], "thuat_ngu": []})
        text = 'Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: 拖拉机 → máy cày kéo\n{"id": "c1", "goc": "拖拉机"}'
        self.assertEqual(mock.answer("", text)["lines"], [{"id": "c1", "text": "Câu thử 1 đã dịch máy cày kéo"}])
        self.assertEqual(mock.answer("", text, drop_terms=True)["lines"], [{"id": "c1", "text": "Câu thử 1 đã dịch 拖拉机"}])


def glossary_term(source, target, mandatory=False):
    return {"source": source, "target": target, "mandatory": mandatory}


if __name__ == "__main__":
    unittest.main()

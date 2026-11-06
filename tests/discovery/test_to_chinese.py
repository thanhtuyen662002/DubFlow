"""Vietnamese → Chinese search phrase, with the web translators replayed offline."""

import io
import json
import unittest
from urllib.error import HTTPError, URLError

from engine.dubflow.discovery.to_chinese import TranslateError, main, to_chinese


def google_body(*parts: str) -> bytes:
    return json.dumps([[[part, "src", None, None] for part in parts], None, "vi"]).encode()


class ToChineseTest(unittest.TestCase):
    def test_google_answer_is_joined_and_cleaned(self):
        seen = []

        def send(request, timeout):
            seen.append(request.full_url)
            return google_body("养猪", "大楼。")

        self.assertEqual(to_chinese("  tháp   nuôi lợn ", send), ("养猪大楼", "google"))
        self.assertEqual(len(seen), 1)
        self.assertIn("sl=vi", seen[0])
        self.assertIn("tl=zh-CN", seen[0])
        self.assertIn("q=th%C3%A1p+nu%C3%B4i+l%E1%BB%A3n", seen[0])

    def test_chinese_input_is_kept_without_a_request(self):
        def send(request, timeout):
            raise AssertionError("no request expected")

        self.assertEqual(to_chinese("仙侠 剧", send), ("仙侠 剧", "none"))

    def test_microsoft_is_the_fallback(self):
        def send(request, timeout):
            url = request.full_url
            if "googleapis" in url:
                raise HTTPError(url, 429, "Too Many Requests", None, None)
            if url.endswith("/translate/auth"):
                return b"token123"
            self.assertEqual(request.get_header("Authorization"), "Bearer token123")
            self.assertEqual(json.loads(request.data), [{"Text": "tiên hiệp"}])
            return json.dumps([{"translations": [{"text": "仙侠", "to": "zh-Hans"}]}]).encode()

        self.assertEqual(to_chinese("tiên hiệp", send), ("仙侠", "microsoft"))

    def test_untranslated_answers_are_refused(self):
        def send(request, timeout):
            if "googleapis" in request.full_url:
                return google_body("tien hiep")
            raise HTTPError(request.full_url, 403, "Forbidden", None, None)

        with self.assertRaises(TranslateError) as caught:
            to_chinese("tien hiep", send)
        self.assertEqual(caught.exception.code, "UNAVAILABLE")

    def test_no_network(self):
        def send(request, timeout):
            raise URLError("offline")

        with self.assertRaises(TranslateError) as caught:
            to_chinese("ẩm thực", send)
        self.assertEqual(caught.exception.code, "NETWORK")

    def test_cli_prints_one_ascii_json_line(self):
        out = io.StringIO()
        self.assertEqual(main(["--", "ẩm thực"], send=lambda request, timeout: google_body("美食"), stdout=out), 0)
        line = out.getvalue().strip()
        self.assertTrue(line.isascii())
        self.assertEqual(json.loads(line), {"ok": True, "text": "美食", "engine": "google"})

        out = io.StringIO()
        main(["--", "ẩm thực"], send=lambda request, timeout: (_ for _ in ()).throw(URLError("x")), stdout=out)
        self.assertEqual(json.loads(out.getvalue())["code"], "NETWORK")


if __name__ == "__main__":
    unittest.main()

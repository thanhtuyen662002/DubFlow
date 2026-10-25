"""Offline posting fixtures; discover this directory to avoid tests package clashes."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from engine.dubflow.seo import suggest_post
from engine.dubflow.seo.suggest import STOPWORDS, _hashtag


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {
    "tien_hiep": {
        "source_title": "Tiên hiệp: Bí mật linh kiếm thức tỉnh",
        "source_tags": ["tu tiên", "cổ trang", "phim"],
        "duration_s": 78.5,
        "cues": [
            "Ừ.",
            "Thanh linh kiếm này đã ngủ yên suốt 300 năm!",
            "Vì sao một đệ tử tu tiên lại đánh thức được linh kiếm?",
            "Linh kiếm chỉ nhận người giữ lời hứa với sư phụ.",
            "Bí mật của linh kiếm khiến cả tiên môn chấn động.",
            "Bộ phim tiên hiệp mở ra trận chiến giữa hai môn phái.",
        ],
    },
    "cooking": {
        "source_title": "Đậu phụ sốt cay: Món ăn Tứ Xuyên dễ nấu",
        "source_tags": ["ẩm thực", "món ăn", "nấu ăn"],
        "duration_s": 52.0,
        "cues": [
            "Nào!",
            "Chỉ 10 phút là có đậu phụ sốt cay thơm lừng!",
            "Cắt đậu phụ thành miếng nhỏ rồi chần qua nước nóng.",
            "Phi thơm tỏi, thêm thịt băm và tương ớt vào chảo.",
            "Cho đậu phụ vào sốt cay, nấu nhỏ lửa trong 3 phút.",
            "Món ăn Tứ Xuyên này ăn cùng cơm nóng rất ngon.",
        ],
    },
    "comedy": {
        "source_title": "Tấu hài: Chàng giao hàng nhận nhầm nhà",
        "source_tags": ["hài hước", "tình huống"],
        "duration_s": 35.0,
        "cues": [
            "Dạ.",
            "Anh giao hàng gõ cửa đúng 3 lần mà chẳng ai trả lời!",
            "Sao đơn giao hàng lại ghi địa chỉ chuồng gà?",
            "Chàng giao hàng vừa quay lưng thì chú gà chạy tới.",
            "Hóa ra ông chủ đặt đồ ăn cho cả đàn gà.",
            "Màn tấu hài kết thúc khi chú gà giành luôn túi đồ ăn!",
        ],
    },
}


class SuggestPostTests(unittest.TestCase):
    def assert_shape(self, result):
        self.assertEqual(set(result), {"titles", "description", "hashtags", "keywords"})
        self.assertIsInstance(result["titles"], list)
        self.assertGreaterEqual(len(result["titles"]), 3)
        self.assertLessEqual(len(result["titles"]), 5)
        self.assertEqual(len(result["titles"]), len({title.casefold() for title in result["titles"]}))
        for title in result["titles"]:
            self.assertTrue(title.strip())
            self.assertLessEqual(len(title), 100)
            self.assertNotIn("\n", title)
            first_letter = next(char for char in title if char.isalpha())
            self.assertEqual(first_letter, first_letter.upper())
        self.assertIsInstance(result["description"], str)
        self.assertLessEqual(len(result["description"]), 300)
        self.assertTrue(result["description"].endswith("#vietsub"))
        self.assertNotIn("dubflow", result["description"].lower())
        self.assertIsInstance(result["keywords"], list)
        for keyword in result["keywords"]:
            self.assertEqual(keyword, unicodedata.normalize("NFC", keyword.lower()))
            self.assertNotIn(keyword.split()[0], STOPWORDS)
            self.assertNotIn(keyword.split()[-1], STOPWORDS)
        self.assertGreaterEqual(len(result["hashtags"]), 8)
        self.assertLessEqual(len(result["hashtags"]), 15)
        self.assertEqual(len(result["hashtags"]), len(set(result["hashtags"])))
        self.assertIn("#vietsub", result["hashtags"])
        self.assertIn("#trungquoc", result["hashtags"])
        for tag in result["hashtags"]:
            self.assertTrue(tag.isascii())
            self.assertRegex(tag, r"^#[a-z0-9]{2,29}$")

    def test_three_genres_and_determinism(self):
        expected = {
            "tien_hiep": {"#tienhiep", "#phimtienhiep", "#cotrang", "#reviewphim", "#phimhay"},
            "cooking": {"#amthuc", "#nauan"},
            "comedy": {"#haihuoc"},
        }
        for name, fixture in FIXTURES.items():
            with self.subTest(genre=name):
                result = suggest_post(**fixture)
                self.assert_shape(result)
                self.assertTrue(expected[name].issubset(result["hashtags"]))
                self.assertTrue(result["keywords"])
                self.assertEqual(result, suggest_post(**fixture))
                self.assertTrue(any("Vietsub" in title for title in result["titles"]))

    def test_stopwords_never_become_keywords(self):
        self.assertGreaterEqual(len(STOPWORDS), 120)
        self.assertEqual(suggest_post([" ".join(sorted(STOPWORDS))])["keywords"], [])
        self.assertEqual(suggest_post(["là của và có không được này đó thì mà cho với một"])["keywords"], [])
        result = suggest_post(["và linh kiếm của", "đang linh kiếm với", "linh kiếm là"])
        self.assertEqual(result["keywords"], ["linh kiếm"])

    def test_frequency_title_bonus_and_overlap_removal(self):
        result = suggest_post(["linh kiếm; linh kiếm; linh kiếm; bánh ngọt"])
        self.assertEqual(result["keywords"][0], "linh kiếm")
        result = suggest_post(["linh kiếm; linh kiếm; bánh ngọt"], source_title="Bánh ngọt")
        self.assertEqual(result["keywords"][0], "bánh ngọt")
        result = suggest_post(["tiên hiệp đẹp"] * 3)
        self.assertEqual(result["keywords"], ["tiên hiệp đẹp"])
        self.assertIn("#tienhiepdep", result["hashtags"])
        result = suggest_post(["đậu phụ sốt cay"] * 3)
        self.assertFalse({"đậu phụ sốt", "phụ sốt cay"}.issubset(result["keywords"]))
        for fixture in FIXTURES.values():
            keywords = suggest_post(**fixture)["keywords"]
            for index, left in enumerate(keywords):
                for right in keywords[index + 1:]:
                    a, b = left.split(), right.split()
                    for size in range(1, min(len(a), len(b)) + 1):
                        self.assertNotEqual(a[-size:], b[:size])
                        self.assertNotEqual(b[-size:], a[:size])

    def test_punctuation_and_cue_boundaries_do_not_form_phrases(self):
        result = suggest_post(["linh kiếm. bánh ngọt", "huyền thoại", "ẩm thực"])
        self.assertNotIn("kiếm bánh", result["keywords"])
        self.assertNotIn("thoại ẩm", result["keywords"])
        self.assertNotIn("ngọt huyền thoại", result["keywords"])

    def test_unicode_normalization_and_hashtag_folding(self):
        fixture = FIXTURES["tien_hiep"]
        decomposed = {**fixture, "cues": [unicodedata.normalize("NFD", cue) for cue in fixture["cues"]],
                      "source_title": unicodedata.normalize("NFD", fixture["source_title"])}
        self.assertEqual(suggest_post(**fixture), suggest_post(**decomposed))
        self.assertEqual(_hashtag("Tiên hiệp đẹp"), "#tienhiepdep")
        self.assertEqual(_hashtag("ĐẬU phụ – SỐT CAY!"), "#dauphusotcay")
        self.assertEqual(_hashtag(unicodedata.normalize("NFD", "đẹp")), "#dep")
        self.assertEqual(_hashtag("中 😀"), "")
        self.assertEqual(_hashtag("x" * 30), "")

    def test_empty_and_whitespace_inputs(self):
        for cues in ([], ["", "   ", "\n\t"], ["???", "!!!", "123"]):
            with self.subTest(cues=cues):
                result = suggest_post(cues)
                self.assert_shape(result)
                self.assertEqual(result["keywords"], [])
                self.assertTrue(any("Video tiếng Trung" in title for title in result["titles"]))
        result = suggest_post(["", "\t"], source_title="Chuyện nhỏ trong căn bếp")
        self.assert_shape(result)
        self.assertIn("Chuyện nhỏ trong căn bếp (Vietsub)", result["titles"])

    def test_hook_is_taken_from_the_most_hooky_cue(self):
        result = suggest_post(["Thanh kiếm nằm yên trong hang đá.", "Vì sao 3 thanh kiếm cùng thức tỉnh?!"])
        self.assertIn("Vì sao 3 thanh kiếm cùng thức tỉnh?!", result["titles"])

    def test_description_uses_first_meaningful_cues(self):
        result = suggest_post(["Ừ!", "Có!", "Cắt đậu phụ thành từng miếng nhỏ.",
                               "Đun nước sốt trên lửa nhỏ!", "Đoạn này không cần lấy."])
        self.assertEqual(result["description"], "Cắt đậu phụ thành từng miếng nhỏ. Đun nước sốt trên lửa nhỏ!\n#vietsub")
        one = suggest_post(["Đây là câu chuyện về thanh kiếm."])
        self.assertIn("Đây là câu chuyện về thanh kiếm.", one["description"])
        self.assertEqual(len(re.findall(r"[.!?](?:\s|$)", one["description"])), 2)

    def test_long_text_keeps_limits_and_complete_words(self):
        word = "nghiêng"
        result = suggest_post([("cánh đồng " + word + " ") * 100], source_title=(word + " ") * 100)
        self.assert_shape(result)
        for title in result["titles"]:
            self.assertNotRegex(title, r"nghiê(?:n)?$")
        self.assert_shape(suggest_post(["x" * 500], source_title="y" * 500))

    def test_remaining_genres_tags_and_word_boundaries(self):
        for text, tags in (("mèo", {"#thucung"}), ("chó", {"#thucung"}),
                           ("game", {"#game"}), ("cổ trang", {"#cotrang"}),
                           ("tu tiên", {"#tienhiep", "#phimtienhiep"})):
            with self.subTest(text=text):
                self.assertTrue(tags.issubset(suggest_post([text])["hashtags"]))
        self.assertNotIn("#game", suggest_post(["gameplay"])["hashtags"])
        self.assertNotIn("#haihuoc", suggest_post(["Hai người đang đi dạo."])["hashtags"])
        result = suggest_post([], source_tags=["#tienhiep", "#tienhiep", "#Đồ ăn"])
        self.assert_shape(result)
        self.assertIn("#phimtienhiep", result["hashtags"])
        self.assertIn("#doan", result["hashtags"])

    def test_genres_are_detected_from_cues_without_metadata(self):
        for name, tags in (("tien_hiep", {"#tienhiep", "#phimtienhiep", "#reviewphim"}),
                           ("cooking", {"#amthuc", "#nauan"}), ("comedy", {"#haihuoc"})):
            with self.subTest(genre=name):
                self.assertTrue(tags.issubset(suggest_post(FIXTURES[name]["cues"])["hashtags"]))

    def test_all_genres_still_keep_required_tags_and_count(self):
        result = suggest_post(["tiên hiệp, cổ trang, ẩm thực, hài, phim, game, thú cưng"])
        self.assert_shape(result)
        self.assertTrue({"#tienhiep", "#phimtienhiep", "#cotrang", "#amthuc", "#nauan",
                         "#haihuoc", "#reviewphim", "#phimhay", "#game", "#thucung"}.issubset(result["hashtags"]))

    def test_does_not_modify_inputs_or_use_network(self):
        cues, tags = ["Nấu món ăn ngon trong căn bếp."], ["ẩm thực"]
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assert_shape(suggest_post(cues, source_tags=tags, duration_s=12.5))
        self.assertEqual(cues, ["Nấu món ăn ngon trong căn bếp."])
        self.assertEqual(tags, ["ẩm thực"])


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", "-m", "engine.dubflow.seo", *args],
                              cwd=ROOT, capture_output=True, text=True, encoding="ascii", timeout=15)

    def assert_json_line(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        self.assertTrue(completed.stdout.isascii())
        return json.loads(completed.stdout)

    def test_success_matches_api_and_overrides_title(self):
        fixture = FIXTURES["cooking"]
        with tempfile.TemporaryDirectory(dir=ROOT / "tests" / "seo") as folder:
            path = Path(folder) / "timeline.json"
            path.write_text(json.dumps({"schema_version": 1, "time_base": {"numerator": 1, "denominator": 1000},
                                        "cues": [{"translated_text": cue, "source_text": "中文"} for cue in fixture["cues"]]},
                                       ensure_ascii=False), encoding="utf-8-sig")
            result = self.assert_json_line(self.run_cli(str(path), "--source-title", fixture["source_title"]))
            self.assertTrue(result.pop("ok"))
            self.assertEqual(result, suggest_post(fixture["cues"], source_title=fixture["source_title"]))

    def test_missing_blank_or_null_translations_are_safe(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests" / "seo") as folder:
            path = Path(folder) / "timeline.json"
            for cues in ([], [{}, {"source_text": "中文"}, {"translated_text": None}, {"translated_text": " "}]):
                path.write_text(json.dumps({"cues": cues}), encoding="utf-8")
                result = self.assert_json_line(self.run_cli(str(path)))
                self.assertTrue(result["ok"])
                self.assertEqual(result["keywords"], [])

    def test_errors_are_json_and_exit_zero(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests" / "seo") as folder:
            path = Path(folder) / "timeline.json"
            for data, code in ((b"{", "INVALID_JSON"), (b"\xff", "INVALID_JSON"),
                               (b"null", "INVALID_TIMELINE"), (b"[]", "INVALID_TIMELINE"),
                               (b"{}", "INVALID_TIMELINE"), (b'{"cues":{}}', "INVALID_TIMELINE"),
                               (b'{"cues":[1]}', "INVALID_TIMELINE"),
                               (b'{"cues":[{"translated_text":42}]}', "INVALID_TIMELINE"),
                               (b'{"cues":[{"translated_text":[]}]}', "INVALID_TIMELINE")):
                with self.subTest(data=data):
                    path.write_bytes(data)
                    result = self.assert_json_line(self.run_cli(str(path)))
                    self.assertEqual(set(result), {"ok", "code", "message"})
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["code"], code)
                    self.assertTrue(result["message"])
            result = self.assert_json_line(self.run_cli(str(Path(folder) / "missing.json")))
            self.assertEqual(result["code"], "TIMELINE_READ_ERROR")

    def test_usage_errors_exit_nonzero(self):
        completed = self.run_cli()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("usage:", completed.stderr)


if __name__ == "__main__":
    unittest.main()

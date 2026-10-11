"""Public multiline descriptions must not reject a verified selected video."""
import unittest
from unittest.mock import Mock

from engine.dubflow.download.bilibili.adapter import BilibiliSourceAdapter
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode

BVID = "BV1o4411M71o"
URL = f"https://www.bilibili.com/video/{BVID}?p=2"


def adapter(description, *, title="authored public part 2"):
    sdk = Mock()
    sdk.inspect_url.return_value = {"id": BVID + "_p2", "title": title,
        "description": description, "duration": 9,
        "formats": [{"format_id": "av", "url": "https://cdn.example.test/second.mp4",
            "vcodec": "h264", "acodec": "aac", "ext": "mp4"}]}
    return BilibiliSourceAdapter(YtDlpProviderTransport("bilibili", authenticated_transport=sdk)), sdk


class DescriptionTests(unittest.TestCase):
    def test_sdk_multiline_description_preserves_words_and_selected_part(self):
        subject, sdk = adapter("  第一句\n第二句\r\n第三句\r第四句\t第五句  ")
        item = subject.inspect(URL)
        self.assertEqual(item.description, "第一句 第二句 第三句 第四句 第五句")
        self.assertEqual(item.to_dict()["description"], item.description)
        self.assertEqual(item.identity.identity_key, "bilibili:" + BVID + "_p2")
        self.assertEqual(item.identity.canonical_url, URL)
        sdk.inspect_url.assert_called_once_with(URL, provider_id="bilibili", headers=None)

    def test_missing_and_empty_optional_description_do_not_reject_media(self):
        for description in (None, "", " ", " \r\n\t "):
            with self.subTest(description=description):
                subject, _ = adapter(description)
                item = subject.inspect(URL)
                self.assertIsNone(item.description)
                self.assertIsNone(item.to_dict()["description"])
                self.assertEqual(len(item.media_candidates), 1)

    def test_raw_description_budget_is_enforced_before_whitespace_normalization(self):
        subject, _ = adapter("字" * 16_384)
        self.assertEqual(len(subject.inspect(URL).description), 16_384)
        for description in ("字" * 16_385, "\n" * 16_385, 7, [], {}):
            with self.subTest(type=type(description).__name__):
                subject, _ = adapter(description)
                with self.assertRaises(SourceError) as caught:
                    subject.inspect(URL)
                self.assertEqual(caught.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_other_description_controls_and_strict_titles_still_refuse(self):
        for control in ("\x00", "\x07", "\x08", "\x0b", "\x0c", "\x1b", "\x1f", "\x7f"):
            for description in ("first" + control + "last", control):
                with self.subTest(control=ord(control), text=bool(description.strip())):
                    subject, _ = adapter(description)
                    with self.assertRaises(SourceError) as caught:
                        subject.inspect(URL)
                    self.assertEqual(caught.exception.code, SourceErrorCode.SOURCE_CHANGED)
        for title in ("first\nlast", "first\tlast", "x" * 4097):
            subject, _ = adapter("valid\npublic description", title=title)
            with self.assertRaises(SourceError) as caught:
                subject.inspect(URL)
            self.assertEqual(caught.exception.code, SourceErrorCode.SOURCE_CHANGED)

    def test_sdk_empty_title_keeps_the_existing_verified_identity_fallback(self):
        subject, _ = adapter("public\ndescription", title="")
        self.assertEqual(subject.inspect(URL).title, BVID + "_p2")
        # A direct API response with an empty title remains a typed refusal.
        transport = Mock()
        transport.fetch_video.return_value = {"data": {"bvid": BVID, "part": 2,
            "title": "", "desc": "public\ndescription"}}
        with self.assertRaises(SourceError) as caught:
            BilibiliSourceAdapter(transport).inspect(URL)
        self.assertEqual(caught.exception.code, SourceErrorCode.SOURCE_CHANGED)


if __name__ == "__main__":
    unittest.main()

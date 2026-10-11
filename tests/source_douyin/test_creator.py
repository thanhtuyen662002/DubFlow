from __future__ import annotations

import json
import unittest

from engine.dubflow.download.douyin.creator import creator_page
from engine.dubflow.download.enumeration.sdk import sdk_page
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode

URL = "https://www.douyin.com/user/MS4wRecordedPublicCreator"


def page(entries, *, more=False, position=0):
    return {"schema_version": 1, "max_cursor": position, "has_more": more, "entries": entries}


class RecordedTransport:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []
        self.pins = {"sdk_archive": "a" * 64, "helper": "b" * 64, "douyin_creator": "c" * 64, "douyin_signer": "d" * 64}

    def enumerate_douyin_page(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.pages.pop(0)

    def enumerate_url(self, *args, **kwargs):
        raise AssertionError("Douyin creators must not enter GenericIE/offset extraction")


class DouyinCreatorTests(unittest.TestCase):
    def test_unpaired_surrogate_title_keeps_neighbor_identities_and_valid_emoji(self):
        for bad in ("bad\ud800title", "bad\udffftitle"):
            with self.subTest(title=ascii(bad)):
                transport = RecordedTransport(page([
                    {"id": "123456", "title": "Before"},
                    {"id": "234567", "title": bad},
                    {"id": "345678", "title": "After \U0001f600"},
                ]))
                result = creator_page(transport, URL, cursor=None, page_size=3)
                self.assertEqual([item.identity.source_id for item in result.items], ["123456", "345678"])
                self.assertEqual([(item.source_id, item.code) for item in result.failures],
                                 [("234567", SourceErrorCode.SOURCE_CHANGED)])
                self.assertEqual(result.items[-1].title, "After \U0001f600")
                for item in result.items:
                    item.title.encode("utf-8", errors="strict")

    def test_provider_cursor_resume_after_process_reopen_and_partial_page(self):
        first_transport = RecordedTransport(page([{"id": "123456", "title": "First"}, None], more=True, position=900))
        first = sdk_page(first_transport, "douyin", URL, cursor=None, page_size=100, session={"Cookie": "synthetic-secret"})
        self.assertFalse(first.completed)
        self.assertEqual(first_transport.calls[0][1], {"position": 0, "page_size": 20, "headers": {"Cookie": "synthetic-secret"}})
        self.assertNotIn("synthetic-secret", first.next_cursor)
        self.assertEqual([f.code for f in first.failures], [SourceErrorCode.NOT_FOUND])
        reopened = RecordedTransport(page([{"id": "234567", "title": "Second"}], more=True, position=600),
                                     page([{"id": "345678", "title": "Last"}]))
        second = creator_page(reopened, URL, cursor=first.next_cursor, page_size=2)
        third = creator_page(reopened, URL, cursor=second.next_cursor, page_size=2)
        self.assertEqual([call[1]["position"] for call in reopened.calls], [900, 600])
        self.assertEqual([item.identity.source_id for item in third.items], ["345678"])
        self.assertTrue(third.completed)
        self.assertIsNone(third.next_cursor)
        self.assertEqual(json.loads(second.next_cursor)["offset"], 3)

    def test_poisoned_private_deleted_and_unsupported_items_keep_stable_ids_and_neighbors(self):
        transport = RecordedTransport(page([{"id": "123456", "title": "Available"},
            {"id": "234567", "error_code": "PRIVATE"}, {"id": "345678", "error_code": "NOT_FOUND"},
            {"id": "456789", "error_code": "UNSUPPORTED"}, {"id": "567890", "title": "bad\0title"}, None,
            {"id": "678901", "title": "Available neighbor"}]))
        result = creator_page(transport, URL, cursor=None, page_size=10)
        self.assertEqual([item.identity.identity_key for item in result.items], ["douyin:123456", "douyin:678901"])
        self.assertEqual([(f.source_id, f.code) for f in result.failures[:4]], [
            ("234567", SourceErrorCode.PRIVATE), ("345678", SourceErrorCode.NOT_FOUND),
            ("456789", SourceErrorCode.UNSUPPORTED), ("567890", SourceErrorCode.SOURCE_CHANGED)])
        self.assertEqual(result.failures[-1].code, SourceErrorCode.NOT_FOUND)

    def test_cursor_other_channel_recipe_version_numbers_and_duplicate_fields_refused_before_request(self):
        base = RecordedTransport(page([{"id": "123456", "title": "One"}], more=True, position=900))
        original = creator_page(base, URL, cursor=None, page_size=1).next_cursor
        value = json.loads(original)
        cases = [dict(value, v=True), dict(value, v=1), dict(value, position=True), dict(value, position=1.0),
                 dict(value, position=0), dict(value, position=2**63), dict(value, offset=0),
                 dict(value, offset=10000), dict(value, offset=1.0), dict(value, provider="bilibili"),
                 dict(value, channel="0" * 64), dict(value, recipe="0" * 64), dict(value, extra="token")]
        for changed in cases:
            fresh = RecordedTransport()
            with self.subTest(changed=changed), self.assertRaises(SourceError) as error:
                creator_page(fresh, URL, cursor=json.dumps(changed), page_size=1)
            self.assertEqual(error.exception.code, SourceErrorCode.CHECKPOINT_INVALID)
            self.assertEqual(fresh.calls, [])
        for changed in ('{"v":2,' + original[1:], original + " " * 1025):
            with self.assertRaises(SourceError):
                creator_page(RecordedTransport(), URL, cursor=changed, page_size=1)
        fresh = RecordedTransport()
        fresh.pins["douyin_signer"] = "e" * 64
        with self.assertRaises(SourceError):
            creator_page(fresh, URL, cursor=original, page_size=1)
        self.assertEqual(fresh.calls, [])

    def test_no_progress_cycles_malformed_shape_and_scan_bound_do_not_complete(self):
        base = RecordedTransport(page([{"id": "123456", "title": "One"}], more=True, position=900))
        original = creator_page(base, URL, cursor=None, page_size=1).next_cursor
        for raw in [page([], more=True, position=600), page([None], more=True, position=900),
                    page([None], more=True, position=901), page([None], more=True, position=0),
                    page([None], more=1, position=600), page([None], more=True, position=1.0),
                    {**page([]), "cookie": "synthetic-secret"}, page([None, None])]:
            with self.subTest(raw=raw), self.assertRaises(SourceError) as error:
                creator_page(RecordedTransport(raw), URL, cursor=original, page_size=1)
            self.assertEqual(error.exception.code, SourceErrorCode.SOURCE_CHANGED)
        at_bound = json.loads(original)
        at_bound["offset"] = 9999
        with self.assertRaises(SourceError) as error:
            creator_page(RecordedTransport(page([None], more=True, position=600)), URL, cursor=json.dumps(at_bound), page_size=1)
        self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)

    def test_page_size_and_missing_backend_fail_before_side_effects(self):
        for size in (True, 1.0, 0, 101):
            transport = RecordedTransport()
            with self.assertRaises(SourceError):
                creator_page(transport, URL, cursor=None, page_size=size)
            self.assertEqual(transport.calls, [])
        class MissingBackend:
            pins = {}
        with self.assertRaises(SourceError) as error:
            creator_page(MissingBackend(), URL, cursor=None, page_size=1)
        self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()

"""The extractor's failure detail reaches the local error log without credentials or signed queries."""
from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

from engine.dubflow.download import authenticated_native
from engine.dubflow.download.authenticated import diagnostic_tail


class ExtractorDiagnosticsTests(unittest.TestCase):
    def test_helper_describes_the_error_chain_on_stderr(self):
        try:
            try:
                raise ConnectionResetError("reset at https://api.bilibili.com/x/player/wbi/playurl?w_rid=abc&wts=1")
            except ConnectionResetError as cause:
                raise RuntimeError("Unable to download JSON metadata") from cause
        except RuntimeError as error:
            stderr = io.StringIO()
            with mock.patch.object(sys, "stderr", stderr):
                authenticated_native._describe_failure(error)
        text = stderr.getvalue()
        self.assertIn("RuntimeError: Unable to download JSON metadata", text)
        self.assertIn("ConnectionResetError: reset at https://api.bilibili.com/x/player/wbi/playurl?<query>", text)
        self.assertNotIn("w_rid", text)

    def test_credential_messages_are_dropped(self):
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            authenticated_native._describe_failure(ValueError("bad Cookie SESSDATA=secret"))
        self.assertNotIn("secret", stderr.getvalue())

    def test_tail_cuts_queries_and_credential_lines(self):
        raw = b"ExtractorError: HTTP Error 412 at https://x.bilibili.com/a?sig=1\nCookie: SESSDATA=secret\n"
        tail = diagnostic_tail(raw)
        self.assertEqual(tail, "ExtractorError: HTTP Error 412 at https://x.bilibili.com/a?<query>")
        self.assertEqual(diagnostic_tail(b""), "")


if __name__ == "__main__":
    unittest.main()

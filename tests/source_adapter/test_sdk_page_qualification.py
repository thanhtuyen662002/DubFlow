"""A substituted bundle input must fail before any SDK/helper execution."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("qualify_sdk_pages.py")
spec = importlib.util.spec_from_file_location("sdk_page_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
DESCRIPTOR = Path(__file__).resolve().parents[2] / "engine/dubflow/download/assets/yt-dlp-sdk-v1.json"


class SDKPageQualificationGuards(unittest.TestCase):
    def test_substituted_helper_is_never_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "helper.py"
            sentinel = Path(temp) / "executed"
            helper.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n")
            with patch.object(probe.importlib.util, "spec_from_file_location") as importer:
                with self.assertRaisesRegex(ValueError, "helper differs"):
                    probe.qualify_sdk_pages(Path(temp) / "missing-sdk.whl", helper, DESCRIPTOR,
                        helper_sha256="0" * 64,
                        descriptor_sha256=hashlib.sha256(DESCRIPTOR.read_bytes()).hexdigest())
                importer.assert_not_called()
            self.assertFalse(sentinel.exists())

    def test_substituted_descriptor_fails_before_archive_or_helper_access(self):
        with tempfile.TemporaryDirectory() as temp:
            descriptor = Path(temp) / "descriptor.json"
            descriptor.write_text('{"filename":"../../foreign.whl"}')
            with self.assertRaisesRegex(ValueError, "descriptor differs"):
                probe.qualify_sdk_pages(Path(temp) / "missing-sdk.whl", Path(temp) / "missing-helper.py", descriptor,
                    helper_sha256="0" * 64, descriptor_sha256="0" * 64)

    def test_network_is_forbidden_and_restored_when_probe_fails(self):
        connect = socket.socket.connect
        def attempted_live(*args, **kwargs):
            with socket.socket() as client:
                client.connect(("127.0.0.1", 9))
        with patch.object(probe, "_qualify", side_effect=attempted_live):
            with self.assertRaisesRegex(RuntimeError, "network forbidden"):
                probe.qualify_sdk_pages(None, None, None, helper_sha256="", descriptor_sha256="")
        self.assertIs(socket.socket.connect, connect)


if __name__ == "__main__":
    unittest.main()

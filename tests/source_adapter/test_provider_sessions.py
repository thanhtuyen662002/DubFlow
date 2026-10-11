from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from engine.dubflow.download.bilibili import BilibiliSourceAdapter
from engine.dubflow.download.douyin import DouyinSourceAdapter
from engine.dubflow.download.provider_transport import YtDlpProviderTransport
from engine.dubflow.download.sessions import ProtectedSessionBridge
from engine.dubflow.download.source_adapter import SourceError


class ProviderSessionTests(unittest.TestCase):
    def test_both_provider_adapters_use_protected_bridge_without_serializing_headers(self):
        for provider, reference, adapter_class in (("bilibili", "BV1AbC2345", BilibiliSourceAdapter),
                                                    ("douyin", "123456789", DouyinSourceAdapter)):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                recorded = {}
                protector = Mock()
                def protect(data, context):
                    recorded[context] = data
                    return b"opaque-recorded-session"
                protector.protect.side_effect = protect
                protector.unprotect.side_effect = lambda data, context: recorded[context]
                bridge = ProtectedSessionBridge(temp, protector=protector, clock=lambda: 1000)
                bridge.save(provider, {"Cookie": "session=synthetic-secret"}, expires_at=1200)
                sdk = Mock()
                sdk.inspect_url.return_value = {"id": reference, "title": "Recorded", "duration": 1,
                    "formats": [{"format_id": "combined", "url": "https://cdn.example.test/video.mp4", "width": 640, "height": 360,
                                 "protocol": "https", "vcodec": "h264", "acodec": "aac"}]}
                transport = YtDlpProviderTransport(provider, authenticated_transport=sdk)
                adapter = adapter_class(transport, session_bridge=bridge)
                item = adapter.inspect(reference)
                self.assertEqual(sdk.inspect_url.call_args.kwargs, {"provider_id": provider, "headers": {"Cookie": "session=synthetic-secret"}})
                self.assertEqual(item.identity.source_id, reference)
                self.assertNotIn("synthetic-secret", str(item.to_dict()))
                bridge.clear(provider)
                sdk.reset_mock()
                with self.assertRaises(SourceError):
                    adapter.inspect(reference)
                sdk.inspect_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class AuthenticatedTransportTests(unittest.TestCase):
    def test_actual_isolated_helper_stdin_cookie_and_metadata_boundary(self):
        # CI's Python and default TEMP may be on different Windows drives.
        # This owned scratch stays beside the development interpreter.
        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory(dir=python.parent) as temp:
            helper = Path(temp) / "authenticated_native.py"
            helper.write_bytes((Path(__file__).resolve().parents[2] / "engine/dubflow/download/authenticated_native.py").read_bytes())
            archive = Path(temp) / "recorded-sdk.zip"
            # Recorded SDK fixture exercises the real child/stdio/import boundary;
            # actual upstream SDK semantics are qualified separately.
            source = '''
from http.cookiejar import CookieJar
class YoutubeDL:
    def __init__(self, options):
        assert options['js_runtimes'] == {} and options['cachedir'] is False
        assert 'http_headers' not in options
        self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, url, download):
        cookies = list(self.cookiejar)
        assert len(cookies) == 1 and cookies[0].value == 'synthetic-secret'
        assert cookies[0].domain == '.bilibili.com' and cookies[0].secure
        assert download is False
        if url.endswith('/fail'):
            raise ValueError('authentication cookie synthetic-secret rejected')
        return {'id':'recorded','title':'fixture','cookies':'synthetic-secret','formats':[{'url':'https://cdn.example.test/video.mp4','acodec':'aac','vcodec':'h264','http_headers':{'Cookie':'synthetic-secret'}}]}
'''
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("yt_dlp/__init__.py", source)
                zipped.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
                zipped.writestr("yt_dlp/version.py", "__version__ = 'recorded-sdk'\n")
            # Deliberately uses the development test interpreter; not clean-machine evidence.
            root = Path(os.path.commonpath([str(python), str(helper)]))
            pins = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                    {"python": python, "helper": helper, "sdk_archive": archive}.items()}
            transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper, sdk_archive=archive, pins=pins, timeout_s=10)
            health = transport.health_check(expected_sdk_version="recorded-sdk")
            self.assertTrue(health["isolated"])
            self.assertTrue(health["no_site"])
            with self.assertRaises(SourceError) as wrong_version:
                transport.health_check(expected_sdk_version="different-sdk")
            self.assertEqual(wrong_version.exception.code, SourceErrorCode.UNSUPPORTED)
            result = transport.inspect_url("https://www.bilibili.com/video/recorded", provider_id="bilibili", headers={"Cookie": "SESSDATA=synthetic-secret"})
            self.assertEqual(result["id"], "recorded")
            self.assertNotIn("synthetic-secret", json.dumps(result))
            with self.assertRaises(SourceError) as context:
                transport.inspect_url("https://www.bilibili.com/fail", provider_id="bilibili", headers={"Cookie": "SESSDATA=synthetic-secret"})
            self.assertEqual(context.exception.code, SourceErrorCode.AUTH_REQUIRED)
            self.assertNotIn("synthetic-secret", str(context.exception))
            archive.write_bytes(b"changed")
            with self.assertRaises(SourceError) as context:
                transport.inspect_url("https://www.bilibili.com/video/recorded", provider_id="bilibili", headers={"Cookie": "SESSDATA=synthetic-secret"})
            self.assertEqual(context.exception.code, SourceErrorCode.UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()

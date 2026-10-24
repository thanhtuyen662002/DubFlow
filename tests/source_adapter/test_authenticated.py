from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class AuthenticatedTransportTests(unittest.TestCase):
    def test_actual_isolated_helper_classifies_failures_without_diagnostics(self):
        cases = [
            ("HTTP Error 401: Unauthorized", 2),
            ("ERROR: [generic] Unable to download webpage: HTTP Error 403: Forbidden (caused by <HTTPError 403: Forbidden>)", 2),
            ("ERROR: [generic] Unable to download webpage: HTTP Error 503: Service Unavailable", 6),
            ("Forbidden", 2),
            ("HTTP 404", 4),
            ("HTTP Error 410: Gone", 4),
            ("Video not found", 4),
            ("Video does not exist", 4),
            ("Video has been removed", 4),
            ("Video was deleted", 4),
            ("HTTP Error 429", 3),
            ("Too many requests", 3),
            ("Unsupported URL", 7),
            ("[BiliBili] Unable to extract initial state; please report this issue on https://github.com/yt-dlp/yt-dlp/issues", 5),
            ("Unable to extract initial state", 5),
            ("Please report this issue", 5),
            ("The read operation timed out", 6),
            ("Connection reset by peer", 6),
            ("Connection refused", 6),
            ("Temporary failure in name resolution", 6),
            ("getaddrinfo failed", 6),
            ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", 6),
            ("Unable to download webpage: connection reset; please report this issue", 6),
            ("Unexpected extractor failure", 5),
            ("Login required", 2),
            ("Private video", 2),
            ("Video unavailable", 4),
        ]
        # The status must beat misleading outer diagnostics, including yt-dlp's
        # nonstandard DownloadError.exc_info -> ExtractorError.cause chain.
        for status, code in [(401, 2), (403, 2), (404, 4), (410, 4), (429, 3), (503, 6)]:
            for chain in ("sdk", "cause", "context"):
                cases.append(({"status": status, "chain": chain}, code))
        cases.append(({"chain": "cycle"}, 5))
        cases.append(({"chain": "unsupported"}, 7))
        for code in range(2, 8):
            cases.append(({"native_exit_code": code}, code))
        cases.append(({"native_exit_code": 99}, 5))

        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory(dir=python.parent) as temp:
            helper = Path(temp) / "authenticated_native.py"
            helper.write_bytes((Path(__file__).resolve().parents[2] / "engine/dubflow/download/authenticated_native.py").read_bytes())
            archive = Path(temp) / "recorded-sdk.zip"
            source = '''
from http.cookiejar import CookieJar
CASES = ''' + repr([value for value, _ in cases]) + '''
SECRET = ' https://cdn.example.test/video.mp4?token=synthetic-secret'
class HTTPError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__('opaque response' + SECRET)
class ExtractorError(Exception):
    def __init__(self, cause):
        self.cause = cause
        super().__init__('Unable to extract; please report this issue' + SECRET)
class DownloadError(Exception):
    pass
class YoutubeDL:
    def __init__(self, options):
        assert options['retries'] == 0 and options['extractor_retries'] == 0
        self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, url, download):
        value = CASES[int(url.rsplit('/', 1)[1])]
        if isinstance(value, str):
            raise DownloadError(value + SECRET)
        error = DownloadError('Unable to extract; please report this issue' + SECRET)
        if 'native_exit_code' in value:
            error.native_exit_code = value['native_exit_code']
        elif value['chain'] == 'sdk':
            cause = ExtractorError(HTTPError(value['status']))
            error.exc_info = (type(cause), cause, None)
        elif value['chain'] == 'cause':
            raise error from HTTPError(value['status'])
        elif value['chain'] == 'context':
            try:
                raise HTTPError(value['status'])
            except HTTPError:
                raise error
        elif value['chain'] == 'cycle':
            error.cause = error
        elif value['chain'] == 'unsupported':
            raise NotImplementedError('extractor missing' + SECRET)
        raise error
'''
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("yt_dlp/__init__.py", source)
                zipped.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
            root = Path(os.path.commonpath([str(python), str(helper)]))
            pins = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                    {"python": python, "helper": helper, "sdk_archive": archive}.items()}
            transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper, sdk_archive=archive, pins=pins, timeout_s=10)
            run = transport.runner.run_with_input
            results = []

            def record(*args, **kwargs):
                result = run(*args, **kwargs)
                results.append(result)
                return result

            codes = {2: SourceErrorCode.AUTH_REQUIRED, 3: SourceErrorCode.RATE_LIMITED,
                     4: SourceErrorCode.NOT_FOUND, 5: SourceErrorCode.SOURCE_CHANGED,
                     6: SourceErrorCode.NETWORK, 7: SourceErrorCode.UNSUPPORTED}
            with patch.object(transport.runner, "run_with_input", side_effect=record):
                for index, (value, code) in enumerate(cases):
                    with self.subTest(failure=value):
                        before = len(results)
                        with self.assertRaises(SourceError) as context:
                            transport.inspect_url(f"https://example.test/{index}", provider_id="generic")
                        self.assertEqual(len(results), before + 1)  # No hidden transport retry.
                        self.assertEqual(results[-1], (code, "", ""))
                        self.assertEqual(context.exception.code, codes[code])
                        self.assertEqual(context.exception.retryable, code in {3, 6})
                        self.assertNotIn("synthetic-secret", json.dumps(context.exception.to_dict()))

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

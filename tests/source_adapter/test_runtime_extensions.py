"""#335: pinned yt-dlp extensions (impersonation, EJS, Deno) for TikTok and YouTube links."""
from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from engine.dubflow.download import runtime
from engine.dubflow.download.authenticated import AuthenticatedYtDlpTransport
from engine.dubflow.download.materializer import DownloadError, HttpResponse
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode
from tests.source_adapter import test_runtime  # A module import: its tests run once, in their own module.


def wheel(members: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


LIB = wheel({"fakeimp/__init__.py": b"VERSION = 'recorded'\n", "fakeimp-1.0.dist-info/licenses/LICENSE": b"lib license",
             "fakeimp-1.0.data/scripts/never.exe": b"never installed"})
DENO_EXE = b"recorded deno executable"
DENO = wheel({"deno-9.9.9.data/scripts/deno.exe": DENO_EXE, "deno-9.9.9.dist-info/licenses/LICENSE": b"deno license"})


def extensions(**changes):
    def pin(name, data, **extra):
        filename = f"{name}-1.0-py3-none-any.whl"
        return {"name": name, "version": "1.0", "target": "lib", "filename": filename,
                "url": "https://files.pythonhosted.org/recorded/" + filename, "size_bytes": str(len(data)),
                "sha256": hashlib.sha256(data).hexdigest(), "spdx": "MIT", **extra}
    deno = {"target": "deno", "member": "deno-9.9.9.data/scripts/deno.exe", "member_size_bytes": str(len(DENO_EXE)),
            "member_sha256": hashlib.sha256(DENO_EXE).hexdigest(), "license_member": "deno-9.9.9.dist-info/licenses/LICENSE"}
    return [pin("fakeimp", LIB), pin("deno", DENO, **{**deno, **changes})]


class UrlTransport:
    def __init__(self, files):
        self.files, self.calls = files, []

    def open(self, url, *, headers=None):
        self.calls.append(url)
        data = self.files[url.rsplit("/", 1)[1]]
        return HttpResponse(200, {"content-length": str(len(data))}, BytesIO(data), url)


class ProfileTests(unittest.TestCase):
    def test_shipped_profile_pins_the_extras_this_yt_dlp_release_pins(self):
        profile = runtime._read_profile(runtime.PROFILE_PATH)
        pins = {item["name"]: item for item in runtime._extensions(profile)}
        self.assertEqual({name: item["version"] for name, item in pins.items()},
                         {"curl-cffi": "0.16.0", "cffi": "2.1.1", "certifi": "2026.7.22", "pycparser": "3.0",
                          "yt-dlp-ejs": "0.8.0", "deno": "2.9.8"})
        self.assertEqual([name for name, item in pins.items() if item["target"] == "deno"], ["deno"])
        self.assertTrue(pins["cffi"]["filename"].endswith("cp312-cp312-win_amd64.whl"))  # The runtime is CPython 3.12.

    def test_unsafe_or_ambiguous_extension_pins_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path, profile, _ = test_runtime.recorded_profile(Path(temp))
            cases = {
                "foreign host": [dict(extensions()[0], url="https://example.com/fakeimp-1.0-py3-none-any.whl"), extensions()[1]],
                "no JS runtime": extensions()[:1],
                "two JS runtimes": [extensions()[1], dict(extensions()[1], name="deno-two")],
                "unsafe member": extensions(member="../deno.exe"),
                "unknown target": [dict(extensions()[0], target="plugin"), extensions()[1]],
            }
            for label, value in cases.items():
                with self.subTest(label):
                    path.write_text(json.dumps(dict(profile, extensions=value)), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        runtime._read_profile(path)


class ProvisionTests(unittest.TestCase):
    def test_build_extracts_libraries_and_only_the_pinned_deno_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, profile, sdk = test_runtime.recorded_profile(root)
            path.write_text(json.dumps(dict(profile, extensions=extensions())), encoding="utf-8")
            files = {profile["filename"]: sdk, "fakeimp-1.0-py3-none-any.whl": LIB, "deno-1.0-py3-none-any.whl": DENO}
            transport = UrlTransport(files)
            with patch.object(runtime, "PROFILE_PATH", path):
                runtime.provision_sdk(root, transport=transport)
                lib = root / runtime.EXTENSION_LIB
                self.assertEqual((lib / "fakeimp/__init__.py").read_bytes(), b"VERSION = 'recorded'\n")
                self.assertFalse(any("never.exe" in str(item) for item in lib.rglob("*")))
                self.assertEqual(sorted(item.name for item in (root / runtime.EXTENSION_DENO).iterdir()), ["LICENSE", "deno.exe"])
                self.assertFalse((root / "source/.extension-wheels").exists())
                (lib / "planted.py").write_bytes(b"import os")
                runtime.provision_sdk(root, transport=transport)  # A rebuild removes anything planted.
                self.assertFalse((lib / "planted.py").exists())
            tampered = dict(extensions()[1], member_sha256="0" * 64)
            path.write_text(json.dumps(dict(profile, extensions=[extensions()[0], tampered])), encoding="utf-8")
            with patch.object(runtime, "PROFILE_PATH", path), self.assertRaises(SourceError):
                runtime.provision_sdk(root, transport=transport)
            with patch.object(runtime, "PROFILE_PATH", path), self.assertRaises((SourceError, DownloadError)):
                runtime.provision_sdk(root, transport=UrlTransport({**files, "deno-1.0-py3-none-any.whl": b"x" * len(DENO)}))


class FactoryTests(unittest.TestCase):
    def bundle(self, root):
        inventory = test_runtime.bundle(root)
        profile_path = root / runtime.BUNDLE_PROFILE
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile_path.write_text(json.dumps(dict(profile, extensions=extensions())), encoding="utf-8")
        item = next(value for value in inventory if value["path"] == runtime.BUNDLE_PROFILE)
        data = profile_path.read_bytes()
        item.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=str(len(data)))
        for name, data in {"runtime/source/ytdlp-lib/fakeimp/__init__.py": b"VERSION = 'recorded'\n",
                           "runtime/source/deno/deno.exe": DENO_EXE, "runtime/source/deno/LICENSE": b"deno license"}.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
            inventory.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": str(len(data))})
        return inventory

    def test_generic_transport_runs_with_verified_extensions_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = self.bundle(root)
            adapter = runtime.provider_from_verified_bundle(root, artifacts=inventory, provider_id="generic")
            transport = adapter._transport._sdk
            self.assertEqual(transport.extensions["deno"], root / "runtime/source/deno/deno.exe")
            self.assertEqual(len(transport.extensions["files"]), 3)
            transport._verify()
            planted = root / "runtime/source/ytdlp-lib/fakeimp/extra.py"
            planted.write_bytes(b"import os")
            with self.assertRaises(SourceError) as raised:
                transport._verify()
            self.assertEqual(raised.exception.code, SourceErrorCode.UNSUPPORTED)
            planted.unlink()
            (root / "runtime/source/ytdlp-lib/fakeimp/__init__.py").write_bytes(b"VERSION = 'changed'\n")
            with self.assertRaises(SourceError):
                transport._verify()

    def test_a_deno_that_differs_from_the_pin_disables_the_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = self.bundle(root)
            changed = copy.deepcopy(inventory)
            item = next(value for value in changed if value["path"] == "runtime/source/deno/deno.exe")
            (root / item["path"]).write_bytes(b"other executable")
            item.update(sha256=hashlib.sha256(b"other executable").hexdigest(), size_bytes=str(len(b"other executable")))
            with self.assertRaises(SourceError):
                runtime.provider_from_verified_bundle(root, artifacts=changed, provider_id="generic")


class IsolatedHelperTests(unittest.TestCase):
    def test_child_imports_the_extensions_and_gives_yt_dlp_only_the_pinned_deno(self):
        python = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory(dir=python.parent) as temp:
            folder = Path(temp)
            helper = folder / "authenticated_native.py"
            helper.write_bytes((Path(__file__).resolve().parents[2] / "engine/dubflow/download/authenticated_native.py").read_bytes())
            deno = folder / "deno" / "deno.exe"
            deno.parent.mkdir()
            deno.write_bytes(DENO_EXE)
            lib = folder / "lib"
            (lib / "curl_cffi").mkdir(parents=True)
            (lib / "curl_cffi/__init__.py").write_bytes(b"")
            source = f'''
from http.cookiejar import CookieJar
class YoutubeDL:
    def __init__(self, options):
        assert options['js_runtimes'] == {{'deno': {{'path': {str(deno)!r}}}}}, options['js_runtimes']
        assert options['remote_components'] == []
        self.cookiejar = CookieJar()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def extract_info(self, url, download):
        import curl_cffi
        return {{'id': 'clip', 'title': 'TikTok fixture', 'ie_key': 'TikTok', 'webpage_url': url,
                 'formats': [{{'url': 'https://cdn.example.test/v.mp4', 'acodec': 'aac', 'vcodec': 'h264'}}]}}
'''
            archive = folder / "recorded-sdk.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("yt_dlp/__init__.py", source)
                zipped.writestr("yt_dlp/globals.py", "class Value: value = ['default']\nplugin_dirs=Value()\n")
                zipped.writestr("yt_dlp/version.py", "__version__ = 'recorded-sdk'\n")
            root = Path(os.path.commonpath([str(python), str(helper)]))
            pins = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
                    {"python": python, "helper": helper, "sdk_archive": archive}.items()}
            files = {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size)
                     for path in (deno, lib / "curl_cffi/__init__.py")}
            transport = AuthenticatedYtDlpTransport(runtime_root=root, python=python, helper=helper, sdk_archive=archive,
                                                    pins=pins, timeout_s=20, extensions={"lib": lib, "deno": deno, "files": files})
            health = transport.health_check(expected_sdk_version="recorded-sdk")
            self.assertEqual((health["impersonation"], health["js_runtime"]), (True, "deno"))
            self.assertIn(str(lib), health["import_roots"])
            result = transport.inspect_url("https://www.tiktok.com/@someone/video/1", provider_id="generic")
            self.assertEqual(result["id"], "clip")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from engine.dubflow.download import runtime
from engine.dubflow.download.materializer import DownloadError, DownloadErrorCode, HttpResponse
from engine.dubflow.download.source_adapter import SourceError, SourceErrorCode


class RecordedTransport:
    def __init__(self, data):
        self.data, self.calls = data, []

    def open(self, url, *, headers=None):
        self.calls.append((url, headers))
        return HttpResponse(200, {"content-length": str(len(self.data))}, BytesIO(self.data), url)


def recorded_profile(root):
    # Synthetic pinned code/notice, never presented as upstream distribution evidence.
    notice, name = b"recorded test notice", "yt_dlp-2026.8.19-py3-none-any.whl"
    buffer = BytesIO()
    license_path = "yt_dlp-2026.8.19.dist-info/licenses/LICENSE"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(license_path, notice)
    data = buffer.getvalue()
    profile = {"schema_version": 1, "producer_id": "recorded-sdk", "version": "2026.08.19", "filename": name,
               "url": "https://files.pythonhosted.org/recorded/" + name,
               "size_bytes": str(len(data)), "sha256": hashlib.sha256(data).hexdigest(),
               "license": {"spdx": "Unlicense", "approved": True, "redistributable": True,
                           "archive_path": license_path, "size_bytes": str(len(notice)),
                           "sha256": hashlib.sha256(notice).hexdigest()}}
    path = root / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path, profile, data


def bundle(root):
    profile_path, profile, sdk = recorded_profile(root)
    files = {runtime.BUNDLE_PROFILE: profile_path.read_bytes(), runtime.BUNDLE_HELPER: b"recorded helper",
             "runtime/python.exe": b"recorded Python", "runtime/source/"+profile["filename"]: sdk,
             "runtime/media/ffmpeg.exe": b"recorded FFmpeg", "runtime/media/ffprobe.exe": b"recorded FFprobe"}
    artifacts = []
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": str(len(data))})
    return artifacts


class SourceRuntimeTests(unittest.TestCase):
    def test_download_then_reuse_without_network_and_no_archive_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile_path, profile, data = recorded_profile(root)
            transport = RecordedTransport(data)
            with patch.object(runtime, "PROFILE_PATH", profile_path):
                path = runtime.provision_sdk(root, transport=transport)
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(runtime.provision_sdk(root, transport=transport), path)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [profile["filename"]])
                path.write_bytes(b"changed")
                with self.assertRaises(SourceError):
                    runtime.provision_sdk(root, transport=transport)
                self.assertEqual(path.read_bytes(), b"changed")
                self.assertEqual(len(transport.calls), 1)

    def test_unapproved_license_stops_before_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile_path, profile, data = recorded_profile(root)
            profile["license"]["approved"] = False
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            transport = RecordedTransport(data)
            with patch.object(runtime, "PROFILE_PATH", profile_path), self.assertRaises(SourceError):
                runtime.provision_sdk(root, transport=transport)
            self.assertEqual(transport.calls, [])

    def test_checksum_failure_and_cancel_remain_typed_without_final_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile_path, profile, data = recorded_profile(root)
            with patch.object(runtime, "PROFILE_PATH", profile_path):
                with self.assertRaises(DownloadError) as error:
                    runtime.provision_sdk(root, transport=RecordedTransport(b"x" * len(data)))
                self.assertEqual(error.exception.code, DownloadErrorCode.CHECKSUM_MISMATCH)
                self.assertFalse((root / "source" / profile["filename"]).exists())
                with self.assertRaises(DownloadError) as error:
                    runtime.provision_sdk(root, transport=RecordedTransport(data), cancel=lambda: True)
                self.assertEqual(error.exception.code, DownloadErrorCode.CANCELLED)

    def test_factory_carries_approved_pins_and_provider_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = bundle(root)
            for provider in ("bilibili", "douyin"):
                adapter = runtime.provider_from_verified_bundle(root, artifacts=inventory, provider_id=provider)
                self.assertEqual(adapter.provider_id, provider)
                transport = adapter._transport._authenticated
                self.assertEqual(transport.python, root / "runtime/python.exe")
                self.assertEqual(transport.pins["python"], inventory[2]["sha256"])
                self.assertIsNotNone(adapter._stream_materializer)

    def test_mutation_of_each_required_file_blocks_factory_without_repinning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = bundle(root)
            for item in inventory:
                with self.subTest(path=item["path"]):
                    path = root / item["path"]
                    old = path.read_bytes()
                    path.write_bytes(b"x" * len(old))
                    with self.assertRaises(SourceError) as error:
                        runtime.provider_from_verified_bundle(root, artifacts=inventory, provider_id="bilibili")
                    self.assertEqual(error.exception.code, SourceErrorCode.UNSUPPORTED)
                    path.write_bytes(old)

    def test_missing_duplicate_and_wrong_reviewed_sdk_inventory_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = bundle(root)
            cases = [inventory[:-1], inventory + [dict(inventory[0], path=inventory[0]["path"].upper())]]
            for values in cases:
                with self.assertRaises(SourceError):
                    runtime.provider_from_verified_bundle(root, artifacts=values, provider_id="bilibili")
            changed = copy.deepcopy(inventory)
            path = root / changed[3]["path"]
            path.write_bytes(b"not the reviewed wheel")
            changed[3].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=str(path.stat().st_size))
            with self.assertRaises(SourceError):
                runtime.provider_from_verified_bundle(root, artifacts=changed, provider_id="bilibili")

    def test_no_runtime_discovery_or_unknown_provider_fallback(self):
        with self.assertRaises(SourceError):
            runtime.provider_from_verified_bundle("relative", artifacts=[], provider_id="bilibili")
        with self.assertRaises(SourceError) as error:
            runtime.provider_from_verified_bundle(Path.cwd(), artifacts=[], provider_id="generic")
        self.assertEqual(error.exception.code, SourceErrorCode.INVALID_INPUT)

    def test_health_rejects_external_prefix_or_import_search_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = bundle(root)
            health = {"python_prefix": str(root / "runtime"), "python_base_prefix": str(root / "runtime"),
                      "import_roots": [str(root / "runtime/Lib"), str(root / "runtime/source/sdk.whl")]}
            native = unittest.mock.Mock()
            native._transport._authenticated.health_check.return_value = health
            with patch.object(runtime, "provider_from_verified_bundle", return_value=native):
                self.assertEqual(runtime.source_runtime_health(root, artifacts=inventory)["native"], health)
                for changes in ({"python_prefix": str(root.parent)}, {"python_base_prefix": str(root.parent)},
                                {"import_roots": [str(root.parent / "external/Lib")]},
                                {"import_roots": [""]}, {"import_roots": []}):
                    native._transport._authenticated.health_check.return_value = {**health, **changes}
                    with self.assertRaises(SourceError):
                        runtime.source_runtime_health(root, artifacts=inventory)


if __name__ == "__main__":
    unittest.main()

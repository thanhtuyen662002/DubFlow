from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import urllib.error

from engine.dubflow.models import ModelBootstrapError, ensure_model_profile


class _FakeResponse:
    def __init__(
        self,
        chunks: list[bytes | BaseException],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        value = self._chunks.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.closed = True


class ModelDownloadResumeTests(unittest.TestCase):
    def _profile(self, directory: Path, payload: bytes, url: str = "http://models.test/model.bin") -> Path:
        profile = directory / "profile.json"
        profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile_id": "test-profile",
                    "artifacts": [
                        {
                            "id": "fixture-model",
                            "path": "weights/model.bin",
                            "url": url,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return profile

    def _urlopen(self, responses: list[_FakeResponse], requests: list[object]):
        def open_url(request: object, *, timeout: float) -> _FakeResponse:
            self.assertEqual(timeout, 120)
            requests.append(request)
            if not responses:
                self.fail("unexpected extra model request")
            return responses.pop(0)

        return open_url

    def test_interrupted_download_retains_prefix_and_resumes_with_range(self) -> None:
        payload = b"verified-model-payload"
        prefix_length = 7
        first = _FakeResponse([payload[:prefix_length], urllib.error.URLError("connection reset")])
        remainder = payload[prefix_length:]
        second = _FakeResponse(
            [remainder],
            status=206,
            headers={
                "Content-Range": f"bytes {prefix_length}-{len(payload) - 1}/{len(payload)}",
                "Content-Length": str(len(remainder)),
            },
        )
        with TemporaryDirectory(prefix="dubflow-model-resume-") as directory:
            root = Path(directory)
            profile = self._profile(root, payload)
            model_root = root / "models"
            requests: list[object] = []
            with patch(
                "engine.dubflow.models.runtime.urllib.request.urlopen",
                side_effect=self._urlopen([first], requests),
            ):
                with self.assertRaisesRegex(ModelBootstrapError, "MODEL_DOWNLOAD_FAILED"):
                    ensure_model_profile(profile, model_root)

            partial = model_root / "weights" / ".model.bin.partial"
            target = model_root / "weights" / "model.bin"
            self.assertEqual(partial.read_bytes(), payload[:prefix_length])
            self.assertFalse(target.exists())

            with patch(
                "engine.dubflow.models.runtime.urllib.request.urlopen",
                side_effect=self._urlopen([second], requests),
            ):
                report = ensure_model_profile(profile, model_root)

            self.assertTrue(report["ready"])
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(partial.exists())
            self.assertIsNone(requests[0].get_header("Range"))
            self.assertEqual(requests[1].get_header("Range"), f"bytes={prefix_length}-")

    def test_ignored_range_restarts_cleanly_without_appending_duplicate_bytes(self) -> None:
        payload = b"range-safe-model"
        prefix_length = 4
        first = _FakeResponse([payload], headers={"Content-Length": str(len(payload))})
        second = _FakeResponse([payload], headers={"Content-Length": str(len(payload))})
        with TemporaryDirectory(prefix="dubflow-model-range-") as directory:
            root = Path(directory)
            profile = self._profile(root, payload)
            model_root = root / "models"
            partial = model_root / "weights" / ".model.bin.partial"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(payload[:prefix_length])
            requests: list[object] = []
            with patch(
                "engine.dubflow.models.runtime.urllib.request.urlopen",
                side_effect=self._urlopen([first, second], requests),
            ):
                ensure_model_profile(profile, model_root)

            target = model_root / "weights" / "model.bin"
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(partial.exists())
            self.assertEqual(requests[0].get_header("Range"), f"bytes={prefix_length}-")
            self.assertIsNone(requests[1].get_header("Range"))
            self.assertTrue(first.closed)

    def test_hash_mismatch_removes_untrusted_body_and_enforces_size(self) -> None:
        payload = b"expected-model"
        wrong = b"X" + payload[1:]
        self.assertEqual(len(payload), len(wrong))
        response = _FakeResponse([wrong], headers={"Content-Length": str(len(wrong))})
        with TemporaryDirectory(prefix="dubflow-model-integrity-") as directory:
            root = Path(directory)
            profile = self._profile(root, payload)
            model_root = root / "models"
            requests: list[object] = []
            with patch(
                "engine.dubflow.models.runtime.urllib.request.urlopen",
                side_effect=self._urlopen([response], requests),
            ):
                with self.assertRaisesRegex(ModelBootstrapError, "MODEL_HASH_MISMATCH"):
                    ensure_model_profile(profile, model_root)

            partial = model_root / "weights" / ".model.bin.partial"
            target = model_root / "weights" / "model.bin"
            self.assertFalse(partial.exists())
            self.assertFalse(target.exists())

            oversize = _FakeResponse([payload + b"x"], headers={"Content-Length": str(len(payload) + 1)})
            with patch(
                "engine.dubflow.models.runtime.urllib.request.urlopen",
                side_effect=self._urlopen([oversize], requests),
            ):
                with self.assertRaisesRegex(ModelBootstrapError, "MODEL_SIZE_INVALID"):
                    ensure_model_profile(profile, model_root)
            self.assertFalse(partial.exists())


if __name__ == "__main__":
    unittest.main()

"""Download and verify the pinned model profile used by the CPU product path.

The release contains the profile metadata, not hundreds of megabytes of model
weights.  First use downloads each artifact into the user-owned model root with
an atomic ``.partial`` file and verifies both byte count and SHA-256 before it
becomes visible to a worker.  A failed download can therefore be resumed by
retrying without trusting a partial file or a system-wide model cache.
"""

from __future__ import annotations

from contextlib import closing as _closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request


MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_RANGE = re.compile(r"^bytes\s+(?P<start>[0-9]+)-(?P<end>[0-9]+)/(?P<total>[0-9]+|\*)$")
Progress = Callable[[str, int, int], None]


class ModelBootstrapError(RuntimeError):
    """A deterministic, actionable model bootstrap failure."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        self.code = code[:128]
        self.detail = " ".join(str(detail).replace("\x00", " ").split())[:4096]
        self.retryable = retryable
        super().__init__(f"{self.code}: {self.detail}")


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    relative_path: str
    url: str
    sha256: str
    size_bytes: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModelArtifact":
        if not isinstance(value, Mapping):
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", "artifact must be an object")
        artifact_id = value.get("id")
        relative_path = value.get("path")
        url = value.get("url")
        digest = value.get("sha256")
        size = value.get("size_bytes")
        if not all(isinstance(item, str) and item for item in (artifact_id, relative_path, url, digest)):
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", "artifact id/path/url/hash must be non-empty strings")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_ARTIFACT_BYTES:
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", f"invalid size for {artifact_id}")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", f"invalid SHA-256 for {artifact_id}")
        path = Path(relative_path)
        if path.is_absolute() or "\\" in relative_path or any(part in {"", ".", ".."} for part in path.parts):
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", f"unsafe artifact path for {artifact_id}")
        if not url.startswith("https://"):
            raise ModelBootstrapError("MODEL_MANIFEST_INVALID", f"artifact URL must use HTTPS for {artifact_id}")
        return cls(artifact_id, relative_path.replace("\\", "/"), url, digest, size)


def load_profile(path: Path | str) -> tuple[str, tuple[ModelArtifact, ...]]:
    profile_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelBootstrapError("MODEL_MANIFEST_UNAVAILABLE", f"unable to read {profile_path}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ModelBootstrapError("MODEL_MANIFEST_INVALID", "unsupported model profile schema")
    profile_id = document.get("profile_id")
    artifacts_value = document.get("artifacts")
    if not isinstance(profile_id, str) or not profile_id or not isinstance(artifacts_value, list) or not artifacts_value:
        raise ModelBootstrapError("MODEL_MANIFEST_INVALID", "profile_id and artifacts are required")
    artifacts = tuple(ModelArtifact.from_mapping(item) for item in artifacts_value)
    ids = {item.artifact_id for item in artifacts}
    paths = {item.relative_path.casefold() for item in artifacts}
    if len(ids) != len(artifacts) or len(paths) != len(artifacts):
        raise ModelBootstrapError("MODEL_MANIFEST_INVALID", "model artifact IDs and paths must be unique")
    return profile_id, artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_target(root: Path, relative_path: str) -> Path:
    root = root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ModelBootstrapError("MODEL_PATH_UNSAFE", relative_path) from error
    for parent in [root, *target.parents]:
        if parent == root.parent:
            break
        if parent.exists() and parent.is_symlink():
            raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"symlink in model path: {parent}")
    return target


def _reject_link_components(path: Path, label: str) -> None:
    """Reject symlinks/junctions before resolving a user-owned model root."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor) if absolute.anchor else Path()
    for part in absolute.parts:
        if not part or part == absolute.anchor:
            continue
        current = current / part
        try:
            is_junction = getattr(current, "is_junction", None)
            if current.is_symlink() or (is_junction is not None and is_junction()):
                raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"{label} contains a symlink or junction: {current}")
        except OSError as error:
            raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"unable to inspect {label}: {current}") from error


def _verified(path: Path, artifact: ModelArtifact) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size == artifact.size_bytes and _sha256(path) == artifact.sha256
    except OSError:
        return False


def _partial_path(target: Path) -> Path:
    """Return the stable, private path used for resumable model downloads."""

    return target.with_name(f".{target.name}.partial")


def _partial_size(path: Path, artifact: ModelArtifact) -> int:
    """Validate a resume file without following links or accepting directories."""

    try:
        if path.is_symlink():
            raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"partial download path is not a regular file: {path.name}")
        if not path.exists():
            return 0
        if not path.is_file():
            raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"partial download path is not a regular file: {path.name}")
        size = path.stat().st_size
    except ModelBootstrapError:
        raise
    except OSError as error:
        raise ModelBootstrapError("MODEL_PATH_UNSAFE", f"unable to inspect partial download for {artifact.artifact_id}") from error
    if size > artifact.size_bytes or size > MAX_ARTIFACT_BYTES:
        path.unlink(missing_ok=True)
        return 0
    return size


def _partial_digest(path: Path) -> Any:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest


def _response_status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        getter = getattr(response, "getcode", None)
        value = getter() if callable(getter) else 200
    try:
        status = int(value)
    except (TypeError, ValueError) as error:
        raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", "model server returned an invalid HTTP status", retryable=True) from error
    if not 100 <= status <= 599:
        raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", "model server returned an invalid HTTP status", retryable=True)
    return status


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except (AttributeError, TypeError):
        return None
    return value if isinstance(value, str) else None


def _content_length(response: Any, *, artifact: ModelArtifact, remaining: int) -> int | None:
    value = _response_header(response, "Content-Length")
    if value is None:
        return None
    try:
        length = int(value.strip())
    except (TypeError, ValueError) as error:
        raise ModelBootstrapError("MODEL_SIZE_INVALID", f"server returned an invalid content length for {artifact.artifact_id}") from error
    if length < 0 or length > remaining:
        raise ModelBootstrapError("MODEL_SIZE_INVALID", f"download for {artifact.artifact_id} exceeds the pinned size")
    return length


def _content_range(response: Any) -> tuple[int, int, int] | None:
    value = _response_header(response, "Content-Range")
    if value is None:
        return None
    match = _RANGE.fullmatch(value.strip())
    if match is None or match.group("total") == "*":
        return None
    return int(match.group("start")), int(match.group("end")), int(match.group("total"))


def _close_response(response: Any) -> None:
    try:
        response.close()
    except (AttributeError, OSError):
        pass


def _request(artifact: ModelArtifact, offset: int) -> Any:
    headers = {"User-Agent": "DubFlow/1.0", "Accept": "application/octet-stream"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(artifact.url, headers=headers)
    try:
        return urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        # A stale partial can legitimately produce 416.  The caller treats it
        # as a signal to discard that partial and make one clean request.
        if offset and error.code == 416:
            error.close()
            return None
        raise


def _download(artifact: ModelArtifact, target: Path, progress: Progress | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _partial_path(target)
    offset = _partial_size(temporary, artifact)
    if offset == artifact.size_bytes:
        if _verified(temporary, artifact):
            os.replace(temporary, target)
            return
        temporary.unlink(missing_ok=True)
        offset = 0
    if progress and offset:
        progress(artifact.artifact_id, offset, artifact.size_bytes)

    response: Any = None
    try:
        response = _request(artifact, offset)
        if offset and response is None:
            # A stale range can be rejected with HTTP 416.  Drop only the
            # untrusted partial and retry once from byte zero.
            temporary.unlink(missing_ok=True)
            offset = 0
            response = _request(artifact, 0)
        if offset and response is not None:
            status = _response_status(response)
            content_range = _content_range(response)
            range_is_valid = (
                status == 206
                and content_range is not None
                and content_range[0] == offset
                and content_range[1] >= content_range[0]
                and content_range[1] < content_range[2]
                and content_range[2] == artifact.size_bytes
            )
            if range_is_valid:
                expected_span = content_range[1] - content_range[0] + 1
                content_length = _content_length(response, artifact=artifact, remaining=artifact.size_bytes - offset)
                if content_length is not None and content_length != expected_span:
                    range_is_valid = False
            if not range_is_valid:
                # Some CDNs ignore Range and return 200; others return a
                # malformed 206.  Never append either body to an existing
                # partial.  Close it and restart from byte zero once.
                _close_response(response)
                response = None
                temporary.unlink(missing_ok=True)
                offset = 0
                response = _request(artifact, 0)
        if response is None:
            raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"unable to open model artifact {artifact.artifact_id}", retryable=True)

        status = _response_status(response)
        content_range = _content_range(response)
        if status == 206:
            # A range response is safe only when it starts at the requested
            # offset and declares the manifest's total size.  This also makes
            # a server that returns a partial body for a fresh request fail
            # closed rather than silently publishing a truncated model.
            if content_range is None or content_range[0] != offset or content_range[1] < content_range[0] or content_range[2] != artifact.size_bytes:
                raise ModelBootstrapError("MODEL_RANGE_INVALID", f"invalid content range for {artifact.artifact_id}")
            span = content_range[1] - content_range[0] + 1
            length = _content_length(response, artifact=artifact, remaining=artifact.size_bytes - offset)
            if length is not None and length != span:
                raise ModelBootstrapError("MODEL_RANGE_INVALID", f"content range length mismatch for {artifact.artifact_id}")
        elif status != 200:
            raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"model server returned HTTP {status} for {artifact.artifact_id}", retryable=True)
        elif offset:
            # The range request was ignored.  This branch is only reachable
            # when a response changed between the validation above and now;
            # restart conservatively instead of appending a full body.
            _close_response(response)
            response = _request(artifact, 0)
            if response is None:
                raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"unable to restart model artifact {artifact.artifact_id}", retryable=True)
            status = _response_status(response)
            content_range = _content_range(response)
            if status != 200 or content_range is not None:
                raise ModelBootstrapError("MODEL_RANGE_INVALID", f"server did not provide a clean full response for {artifact.artifact_id}")
            offset = 0

        remaining = artifact.size_bytes - offset
        _content_length(response, artifact=artifact, remaining=remaining)
        digest = _partial_digest(temporary) if offset else hashlib.sha256()
        size = offset
        with _closing(response), temporary.open("ab" if offset else "wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"model server returned invalid bytes for {artifact.artifact_id}", retryable=True)
                size += len(chunk)
                if size > artifact.size_bytes or size > MAX_ARTIFACT_BYTES:
                    raise ModelBootstrapError("MODEL_SIZE_INVALID", f"download for {artifact.artifact_id} exceeds the pinned size")
                digest.update(chunk)
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                if progress:
                    progress(artifact.artifact_id, size, artifact.size_bytes)
        if size != artifact.size_bytes:
            raise ModelBootstrapError(
                "MODEL_DOWNLOAD_FAILED",
                f"download for {artifact.artifact_id} ended before the pinned size",
                retryable=True,
            )
        if digest.hexdigest() != artifact.sha256:
            raise ModelBootstrapError("MODEL_HASH_MISMATCH", f"downloaded bytes for {artifact.artifact_id} failed verification")
        os.replace(temporary, target)
    except ModelBootstrapError as error:
        # A complete body that fails its pinned size/hash or a malformed
        # range is not useful resume state.  Interrupted/network failures
        # retain the verified prefix for the next invocation.
        if error.code in {"MODEL_HASH_MISMATCH", "MODEL_SIZE_INVALID", "MODEL_RANGE_INVALID"}:
            temporary.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"unable to download {artifact.artifact_id}: {error}", retryable=True) from error
    except Exception as error:
        raise ModelBootstrapError("MODEL_DOWNLOAD_FAILED", f"unable to download {artifact.artifact_id}: {error}", retryable=True) from error
    finally:
        if response is not None:
            _close_response(response)


def ensure_model_profile(profile_path: Path | str, model_root: Path | str, *, progress: Progress | None = None) -> dict[str, Any]:
    profile_id, artifacts = load_profile(profile_path)
    requested_root = Path(model_root).expanduser()
    _reject_link_components(requested_root, "model root")
    root = Path(os.path.abspath(os.fspath(requested_root)))
    root.mkdir(parents=True, exist_ok=True)
    _reject_link_components(root, "model root")
    downloaded = 0
    for artifact in artifacts:
        target = _safe_target(root, artifact.relative_path)
        if _verified(target, artifact):
            continue
        if target.exists() and not target.is_file():
            raise ModelBootstrapError("MODEL_PATH_CONFLICT", f"model target is not a file: {target}")
        _download(artifact, target, progress)
        downloaded += 1
    return {"profile_id": profile_id, "artifacts": len(artifacts), "downloaded": downloaded, "ready": True}


__all__ = ["ModelArtifact", "ModelBootstrapError", "ensure_model_profile", "load_profile"]

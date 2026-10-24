"""One-shot source acquisition service owned by the bundled runtime.

The Rust supervisor starts this module for one source item. The service owns
provider inspection and media bytes, but it never opens the supervisor's
SQLite database or mutates a job. JSONL stdout is deliberately tiny and
machine-readable so the supervisor can persist progress and launch the normal
local-file worker after a successful atomic publication.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .generic import GenericUrlAdapter, PythonYtDlpTransport
from .materializer import DownloadError, MediaMaterializer, validate_http_url
from .source_adapter import SourceError, SourceErrorCode


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _safe_root(root: Path, destination: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not destination.is_absolute():
        raise ValueError("source root and destination must be absolute")
    root = root.expanduser().resolve()
    destination = destination.expanduser().resolve(strict=False)
    if destination == root or root not in destination.parents:
        raise ValueError("destination escapes the source root")
    if destination.name in {"", ".", ".."} or any(part in {"", ".", ".."} for part in destination.parts):
        raise ValueError("destination contains an unsafe path component")
    if destination.exists() and destination.is_symlink():
        raise ValueError("destination must not be a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return root, destination


def _candidate(item: Any) -> Any:
    candidates = tuple(getattr(item, "media_candidates", ()))
    eligible = [value for value in candidates if getattr(value, "kind", None) == "progressive" and bool(getattr(value, "has_audio", False))]
    if not eligible:
        eligible = [value for value in candidates if getattr(value, "kind", None) == "progressive"]
    if not eligible:
        raise SourceError(SourceErrorCode.UNSUPPORTED, "the source has no bounded progressive audio/video candidate", provider_id="generic", action="choose_local_file")
    return sorted(
        eligible,
        key=lambda value: (
            not bool(getattr(value, "has_audio", False)),
            -int(getattr(value, "height", 0) or 0),
            str(getattr(value, "candidate_id", "")),
        ),
    )[0]


def acquire(source_url: str, destination: str | Path, root: str | Path) -> dict[str, Any]:
    url = validate_http_url(source_url)
    root_path, destination_path = _safe_root(Path(root), Path(destination))
    # The Python extractor is app-owned and all HTTP bytes still pass through
    # the common bounded materializer. This keeps resumable writes, limits,
    # redirects and checksum publication identical for direct and provider URLs.
    materializer = MediaMaterializer()
    adapter = GenericUrlAdapter(transport=PythonYtDlpTransport(), materializer=materializer)
    item = adapter.inspect(url)
    candidate = _candidate(item)
    _emit(
        {
            "event": "inspected",
            "provider_id": item.identity.provider_id,
            "source_id": item.identity.source_id,
            "title": item.title,
            "candidate_id": candidate.candidate_id,
        }
    )

    def progress(done: int, total: int | None) -> None:
        _emit({"event": "progress", "downloaded_bytes": int(done), "total_bytes": None if total is None else int(total)})

    result = adapter.download(
        item,
        destination_path,
        candidate_id=candidate.candidate_id,
        root=root_path,
        resume=True,
        progress=progress,
    )
    return {
        "event": "completed",
        "provider_id": item.identity.provider_id,
        "source_id": item.identity.source_id,
        "source_url": item.identity.canonical_url,
        "title": item.title,
        "path": str(result.path),
        "size_bytes": result.size_bytes,
        "sha256": "sha256:" + result.sha256,
        "resumed": result.resumed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DubFlow app-owned source acquisition")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    try:
        _emit(acquire(args.source_url, args.destination, args.root))
        return 0
    except SourceError as error:
        _emit({"event": "failed", "error": error.to_dict()})
        return 2
    except DownloadError as error:
        _emit({"event": "failed", "error": error.to_dict()})
        return 2
    except (OSError, ValueError) as error:
        _emit(
            {
                "event": "failed",
                "error": {
                    "schema_version": 1,
                    "code": "INVALID_SOURCE_SERVICE",
                    "condition": " ".join(str(error).split())[:4096],
                    "retryable": False,
                    "action": "choose_local_file",
                },
            }
        )
        return 2
    except Exception as error:  # defensive boundary; no traceback on JSONL stdout
        _emit(
            {
                "event": "failed",
                "error": {
                    "schema_version": 1,
                    "code": "SOURCE_SERVICE_FAILED",
                    "condition": " ".join(str(error).split())[:4096],
                    "retryable": True,
                    "action": "retry",
                },
            }
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["acquire", "main"]

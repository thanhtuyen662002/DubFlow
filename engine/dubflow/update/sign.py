"""Release-side Ed25519 signing of the version-one update manifest."""

from __future__ import annotations

from . import UpdateError, _app_root_last

if __name__ == "__main__":
    _app_root_last()

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

from .trust import KEY_ID, _validate_payload, canonical_payload, setup_name, setup_url, verify_manifest


def sign_manifest(payload: dict, private_key_b64: str) -> dict:
    """Sign a payload with a raw 32-byte seed and verify with its public key."""

    if not isinstance(payload, dict):
        raise UpdateError("MANIFEST_INVALID", "update payload must be an object")
    _validate_payload(payload)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64, validate=True))
    except Exception as exc:
        raise UpdateError("SIGNATURE_INVALID", "signing key must be a base64 raw 32-byte Ed25519 seed") from exc
    try:
        canonical = canonical_payload(payload)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise UpdateError("MANIFEST_INVALID", "manifest is not canonical JSON data") from exc
    # Detach nested dictionaries so later edits to the input cannot change the
    # already signed document returned to the caller.
    document = json.loads(canonical)
    document["signature"] = {
        "algorithm": "ed25519",
        "key_id": KEY_ID,
        "value": base64.b64encode(key.sign(canonical)).decode("ascii"),
    }
    public_key_b64 = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    verify_manifest(document, public_key_b64=public_key_b64)
    return document


def main(argv: Sequence[str] | None = None) -> int:
    _app_root_last()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "candidate"), required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private_key = os.environ.get(args.key_env)
        if not private_key:
            raise UpdateError("SIGNATURE_INVALID", "signing key environment variable is missing")
        digest = hashlib.sha256()
        size = 0
        with args.setup.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        payload = {
            "schema_version": 1,
            "version": args.version,
            "channel": args.channel,
            "published_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "notes": args.notes_file.read_text(encoding="utf-8"),
            "setup": {
                "name": setup_name(args.version),
                "url": setup_url(args.version),
                "sha256": digest.hexdigest(),
                "size_bytes": size,
            },
        }
        document = sign_manifest(payload, private_key)
        args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        response = {"ok": True, "path": str(args.out), "version": args.version}
    except UpdateError as exc:
        response = {"ok": False, "code": exc.code, "message": exc.message}
    except (OSError, UnicodeError):
        response = {"ok": False, "code": "IO_ERROR", "message": "unable to read release inputs or write update manifest"}
    print(json.dumps(response, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

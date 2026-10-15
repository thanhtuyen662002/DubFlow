"""Isolated private editable-copy worker; native owns admission and publication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time


def _volume_id(root: Path) -> str:
    """Fresh OS observation compared to native's pin, never a volume hint."""
    if os.name != "nt":
        # Deterministic POSIX byte fixtures remain possible. Native production
        # storage admission still refuses providers other than qualified NTFS.
        return "posix-device:" + str(root.stat().st_dev)
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_mount = kernel.GetVolumePathNameW
    get_mount.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_mount.restype = wintypes.BOOL
    get_guid = kernel.GetVolumeNameForVolumeMountPointW
    get_guid.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_guid.restype = wintypes.BOOL
    get_type = kernel.GetDriveTypeW
    get_type.argtypes = [wintypes.LPCWSTR]
    get_type.restype = wintypes.UINT
    get_info = kernel.GetVolumeInformationW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    get_mode = kernel.GetThreadErrorMode
    get_mode.argtypes = []
    get_mode.restype = wintypes.DWORD
    set_mode = kernel.SetThreadErrorMode
    set_mode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    set_mode.restype = wintypes.BOOL
    previous = wintypes.DWORD()
    if not set_mode(get_mode() | 1, ctypes.byref(previous)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        mount, guid, filesystem = (ctypes.create_unicode_buffer(n) for n in (32768, 64, 64))
        flags = wintypes.DWORD()
        path = str(root)
        if len(path) >= 32767 or "\x00" in path:
            raise ValueError("editable output path exceeds the OS probe bound")
        if not get_mount(path, mount, len(mount)) or not get_guid(mount.value, guid, len(guid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not get_info(mount.value, None, 0, None, None, ctypes.byref(flags), filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        import re
        match = re.fullmatch(r"\\\\\?\\volume\{([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}\\", guid.value.lower())
        if (match is None or match[1] == "00000000-0000-0000-0000-000000000000"
                or get_type(mount.value) != 3 or filesystem.value != "NTFS"
                or flags.value & 0x00400000 == 0 or flags.value & 0x00080000):
            raise ValueError("editable output requires the native pinned fixed writable NTFS volume")
        return "windows-volume-guid:" + match[1]
    finally:
        if not set_mode(previous.value, None):
            raise ctypes.WinError(ctypes.get_last_error())


def _available_bytes(root: Path) -> int:
    if os.name != "nt":
        return shutil.disk_usage(root).free
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel.GetDiskFreeSpaceExW
    query.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
                      ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
    query.restype = wintypes.BOOL
    available, total, free = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    if not query(str(root), ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
        raise ctypes.WinError(ctypes.get_last_error())
    if available.value > total.value or available.value > free.value:
        raise ValueError("inconsistent caller-available storage observation")
    return available.value


def build_candidate(raw: bytes, expected_context_hash: str) -> dict:
    from hashlib import sha256
    from engine.dubflow.capcut.review_export import (
        _admit, _closed, _decimal, _hash, _load, _plain, _token, create_pack_from_review_bundle,
    )
    from engine.dubflow.capcut.import_pack import ImportPackError, validate_import_pack
    if len(raw) > 64 * 1024 or "sha256:" + sha256(raw).hexdigest() != _hash(expected_context_hash):
        raise ImportPackError("editable worker context differs from native byte pin")
    context = _load(raw)
    _closed(context, {"schema_version", "kind", "current_token", "bundle_root", "bundle_manifest_sha256",
                      "generation", "storage_root", "output_volume_id", "processing_recipe_identity", "maximum_bytes", "minimum_free_bytes"})
    token = _token(context["current_token"])
    _hash(context["bundle_manifest_sha256"])
    _hash(context["processing_recipe_identity"])
    generation = context["generation"]
    if type(generation) is not str or len(generation) != 64 or any(c not in "0123456789abcdef" for c in generation):
        raise ImportPackError("editable native generation key invalid")
    if type(context["schema_version"]) is not int or context["schema_version"] != 1 or context["kind"] != "review_native_editable_pack_context":
        raise ImportPackError("unsupported native editable context")
    root, bundle = Path(context["storage_root"]), Path(context["bundle_root"])
    _plain(root)
    _plain(bundle)
    expected_volume = context["output_volume_id"]
    if type(expected_volume) is not str or _volume_id(root) != expected_volume:
        raise ImportPackError("editable output differs from native OS volume pin")
    maximum = _decimal(context["maximum_bytes"], positive=True)
    reserve = _decimal(context["minimum_free_bytes"], positive=True)
    if maximum > 32 * 1024**3 or reserve < 2 * 1024**3:
        raise ImportPackError("editable storage policy exceeds native quota or weakens reserve")
    namespace = root / ".dubflow-review-media-v1"
    job = sha256(token["job_id"].encode("utf-8")).hexdigest()
    candidate_parent = namespace / job / ".editable-pending-v1" / generation
    output = candidate_parent / "import-pack"
    expected_bundle = namespace / job / ".exports-published-v1" / generation
    _plain(expected_bundle)
    if not os.path.samefile(bundle, expected_bundle):
        raise ImportPackError("editable bundle is outside its native published job generation")
    # Native must hold the output-wide writer lease across this child. The
    # worker checks all physical/orphan bytes but grants no volume/lock authority.
    deadline = time.monotonic() + 720

    def storage_guard(target: Path, remaining: int) -> None:
        if type(remaining) is not int or remaining < 0 or not target.is_relative_to(candidate_parent):
            raise ImportPackError("editable copy escaped native generation staging")
        _plain(root)
        _plain(target)
        if _volume_id(root) != expected_volume:
            raise ImportPackError("editable native output volume changed during copy")
        used = 0
        count = 0
        pending = [(namespace, 0)] if namespace.exists() else []
        while pending:
            directory, depth = pending.pop()
            if depth > 8:
                raise ImportPackError("editable storage inventory depth exceeded")
            _plain(directory)
            for entry in directory.iterdir():
                count += 1
                if count > 4096 or time.monotonic() >= deadline:
                    raise ImportPackError("editable storage inventory/deadline exceeded")
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ImportPackError("editable storage inventory contains a link")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((entry, depth + 1))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    used += info.st_size
                else:
                    raise ImportPackError("editable storage inventory contains a non-regular/multi-link object")
        if used + remaining > maximum or _available_bytes(root) < remaining + reserve:
            raise ImportPackError("editable aggregate quota/fresh reserve unavailable")

    _, value, _ = _admit(bundle, context["bundle_manifest_sha256"], token, deadline)
    if value["generation"] != generation:
        raise ImportPackError("editable context changed the admitted bundle generation")
    if output.exists():
        # Recovery verifies existing copies against exactly this native bundle;
        # no rerender/rewrite or vendor dispatch is used to hide a partial pack.
        pack = validate_import_pack(output)
        expected_id = "review-" + generation
        if pack["pack_id"] != expected_id:
            raise ImportPackError("existing editable candidate belongs to another generation")
    else:
        pack = create_pack_from_review_bundle(bundle, output,
            expected_manifest_hash=context["bundle_manifest_sha256"], expected_current_token=token,
            storage_guard=storage_guard).production.pack.manifest
    # Independent complete input/copy-set binding on both create and recovery.
    _, value, pins = _admit(bundle, context["bundle_manifest_sha256"], token, deadline)
    expected = {"source-video": pins["source_video"]["sha256"], "review-bundle-manifest": context["bundle_manifest_sha256"]}
    mapping = {"final_video": "localized-video", "captions_srt": "vietsub-srt", "captions_ass": "vietsub-ass",
               "corrected_timeline": "canonical-timeline", "qc_report": "qc-report", "original_audio": "source-audio",
               "dialogue_stem": "dialogue-stem", "final_mix": "final-mix"}
    expected.update({asset_id: pins[role]["sha256"] for role, asset_id in mapping.items() if role in pins})
    checked = validate_import_pack(output)
    if checked != pack or checked["schema_version"] != 2 or {asset["asset_id"]: asset["content_hash"] for asset in checked["assets"]} != expected:
        raise ImportPackError("editable candidate does not match the complete native bundle resources")
    storage_guard(output, 0)
    manifest = output / "manifest.json"
    with manifest.open("rb") as stream:
        pack_hash = "sha256:" + sha256(stream.read(16 * 1024 * 1024 + 1)).hexdigest()
    return {"schema_version": 1, "kind": "review_private_editable_pack_candidate",
            "context_sha256": expected_context_hash, "current_token": token,
            "bundle_manifest_sha256": context["bundle_manifest_sha256"], "generation": generation,
            "processing_recipe_identity": context["processing_recipe_identity"],
            "candidate_path": str(output), "candidate_manifest_sha256": pack_hash,
            "pack_format": 2, "resource_count": len(checked["assets"]), "current_generation_checked": False,
            "active_graph_published": False, "correction_ready": False}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--context-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode) or sys.stdin.buffer.read(1) != b"\x01":
            raise ValueError("editable worker requires app-owned Python -I -S -B and native byte gate")
        if __package__ in (None, ""):
            sys.path.append(str(Path(__file__).resolve().parents[3]))
        from engine.dubflow.capcut.review_export import _bytes
        raw = _bytes(args.context, args.context_sha256, None, time.monotonic() + 30, capture=True)
        result = build_candidate(raw, args.context_sha256)
        encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > 64 * 1024:
            raise ValueError("editable worker result exceeds its byte bound")
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    except Exception as error:
        sys.stderr.write(json.dumps({"code": "REVIEW_EDITABLE_PACK_FAILED", "message": str(error)[:2048]}, ensure_ascii=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable editable assembly from a hash-pinned native Review standard bundle.

This adapter reads/copies bytes only. It does not admit live Review state,
execute a producer/model, mutate SQLite or select/launch a vendor application.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable, Iterator, Mapping

from .import_pack import ImportPackAsset, ImportPackError, validate_import_pack
from .production import ProductionPackResult, create_production_pack

_LIMIT = 64 * 1024 * 1024
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_RECIPE = "review-corrected-standard-bundle-v1"
_MANIFEST = "review_export_manifest.json"
_ROLES = (("final_video", "final_vi.mp4"), ("captions_srt", "captions_vi.srt"),
          ("captions_ass", "captions_vi.ass"), ("corrected_timeline", "timeline.json"),
          ("qc_report", "qc_report.json"), ("source_video", None),
          ("original_audio", "original_audio.wav"), ("dialogue_stem", "dialogue_stem.wav"),
          ("final_mix", "final_mix.wav"))


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ImportPackError("Review bundle has missing or unsupported fields")
    return value


def _hash(value: Any) -> str:
    if type(value) is not str or not _HASH.fullmatch(value):
        raise ImportPackError("Review bundle hash is invalid")
    return value


def _decimal(value: Any, *, positive: bool = False) -> int:
    if type(value) is not str or len(value) > 19 or not _DECIMAL.fullmatch(value):
        raise ImportPackError("Review public counters/sizes require canonical decimal strings")
    number = int(value)
    if number > (1 << 63) - 1 or (positive and number == 0):
        raise ImportPackError("Review bundle counter/size is outside its bound")
    return number


def _token(value: Any) -> dict[str, Any]:
    value = _closed(value, {"job_id", "revision", "snapshot_sha256", "graph_sha256", "original_manifest_sha256"})
    if type(value["job_id"]) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value["job_id"]):
        raise ImportPackError("Review bundle job identity invalid")
    _decimal(value["revision"])
    for name in ("snapshot_sha256", "graph_sha256", "original_manifest_sha256"):
        _hash(value[name])
    return value


def _plain(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ImportPackError("Review bundle paths must be absolute without traversal")
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ImportPackError("Review bundle path contains a link/reparse point")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ImportPackError("Review bundle resources must be single-link plain files")
    readonly = bool(getattr(info, "st_file_attributes", 0) & 1) if os.name == "nt" else not bool(info.st_mode & 0o222)
    if not readonly:
        raise ImportPackError("Review bundle resources must remain sealed")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode


@contextmanager
def _regular(path: Path) -> Iterator[Any]:
    _plain(path)
    before = _identity(path.lstat())
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        # OPEN_EXISTING, GENERIC_READ, FILE_SHARE_READ, OPEN_REPARSE_POINT.
        # No write/delete sharing: a copied input cannot be replaced mid-read.
        handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if _identity(os.fstat(stream.fileno())) != before:
            raise ImportPackError("Review bundle file changed while opening")
        yield stream
        _plain(path)
        if _identity(os.fstat(stream.fileno())) != before or _identity(path.lstat()) != before:
            raise ImportPackError("Review bundle file identity changed while reading")


def _bytes(path: Path, expected: str, size: int | None, deadline: float, *, capture: bool = False) -> bytes:
    result = bytearray()
    digest = sha256()
    with _regular(path) as stream:
        actual_size = os.fstat(stream.fileno()).st_size
        if actual_size == 0 or (size is not None and actual_size != size) or (capture and actual_size > _LIMIT):
            raise ImportPackError("Review bundle file size differs from its pin")
        count = 0
        while True:
            if time.monotonic() >= deadline:
                raise ImportPackError("Review bundle validation deadline exceeded")
            chunk = stream.read(32768)
            if not chunk:
                break
            count += len(chunk)
            if count > actual_size:
                raise ImportPackError("Review bundle file grew during validation")
            digest.update(chunk)
            if capture:
                result.extend(chunk)
        if count != actual_size or "sha256:" + digest.hexdigest() != expected:
            raise ImportPackError("Review bundle resource hash differs from its pin")
    return bytes(result)


def _load(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        value = {}
        for name, item in items:
            if name in value:
                raise ImportPackError("Review metadata contains duplicate keys")
            value[name] = item
        return value
    def nonfinite(_):
        raise ImportPackError("Review metadata contains nonfinite values")
    def finite_float(raw):
        value = float(raw)
        if not math.isfinite(value):
            raise ImportPackError("Review metadata contains nonfinite values")
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ImportPackError("Review metadata is not bounded unambiguous UTF-8 JSON") from error
    if type(value) is not dict:
        raise ImportPackError("Review metadata must be an object")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ImportPackError("Review metadata cannot be canonically encoded") from error


def _admit(root: Path, manifest_hash: str, expected_token: Mapping[str, Any], deadline: float):
    _plain(root)
    raw = _bytes(root / _MANIFEST, _hash(manifest_hash), None, deadline, capture=True)
    value = _load(raw)
    _closed(value, {"schema_version", "kind", "recipe", "generation", "current_token", "origin_token", "mode",
                    "current_snapshot_json", "publication_sha256", "retained_render_sha256", "files"})
    current = _token(value["current_token"])
    origin = _token(value["origin_token"])
    if current != _token(dict(expected_token)) or _decimal(current["revision"]) == 0 or origin["revision"] != "0":
        raise ImportPackError("Review bundle differs from the complete expected current token")
    if current["job_id"] != origin["job_id"] or current["original_manifest_sha256"] != origin["original_manifest_sha256"]:
        raise ImportPackError("Review bundle origin identity changed")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "review_corrected_standard_bundle" or value["recipe"] != _RECIPE:
        raise ImportPackError("unsupported Review standard bundle")
    if type(value["mode"]) is not str or value["mode"] not in {"b1", "b2"}:
        raise ImportPackError("Review bundle mode is invalid")
    identity = {name: item for name, item in value.items() if name != "generation"}
    if value["generation"] != sha256(_canonical(identity)).hexdigest() or _canonical(value) != raw:
        raise ImportPackError("Review bundle generation/canonical bytes differ")
    _hash(value["publication_sha256"])
    _hash(value["retained_render_sha256"])
    snapshot_raw = value["current_snapshot_json"]
    if type(snapshot_raw) is not str or len(snapshot_raw.encode("utf-8")) > _LIMIT or "sha256:" + sha256(snapshot_raw.encode("utf-8")).hexdigest() != current["snapshot_sha256"]:
        raise ImportPackError("Review snapshot differs from its current token")
    snapshot = _load(snapshot_raw.encode("utf-8"))
    if snapshot.get("job_id") != current["job_id"] or type(snapshot.get("revision")) is not int or snapshot["revision"] != _decimal(current["revision"]):
        raise ImportPackError("Review snapshot counter/job differs from its token")
    files = value["files"]
    if type(files) is not list or len(files) != (9 if value["mode"] == "b2" else 6):
        raise ImportPackError("Review bundle resource set is incomplete")
    pins = {}
    for entry, (role, filename) in zip(files, _ROLES):
        _closed(entry, {"role", "relative_path", "sha256", "size_bytes"})
        name = entry["relative_path"]
        if entry["role"] != role or type(name) is not str or (filename is not None and name != filename) or (filename is None and not re.fullmatch(r"source\.[a-z0-9]{1,8}", name)):
            raise ImportPackError("Review bundle resource role/order/path changed")
        size = _decimal(entry["size_bytes"], positive=True)
        maximum = 128 * 1024**3 if role in {"source_video", "final_video"} else (2**32 + 7 if role in {"original_audio", "dialogue_stem", "final_mix"} else _LIMIT)
        if size > maximum:
            raise ImportPackError("Review resource exceeds its role bound")
        _bytes(root / name, _hash(entry["sha256"]), size, deadline)
        pins[role] = entry
    if {entry.name for entry in root.iterdir()} != {_MANIFEST, *(entry["relative_path"] for entry in files)}:
        raise ImportPackError("Review bundle contains missing/extra/unregistered files")
    return raw, value, pins


@dataclass(frozen=True)
class ReviewPackResult:
    production: ProductionPackResult
    bundle_manifest_hash: str
    current_token: dict[str, Any]
    generation: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.production.to_dict(), "review_binding": {"schema_version": 1,
                "bundle_manifest_sha256": self.bundle_manifest_hash, "current_token": self.current_token,
                "generation": self.generation, "current_generation_checked": False}}


def create_pack_from_review_bundle(bundle_root: Path, output_root: Path, *, expected_manifest_hash: str,
                                   expected_current_token: Mapping[str, Any],
                                   storage_guard: Callable[[Path, int], None] | None = None) -> ReviewPackResult:
    """Copy a trusted byte bundle through the existing production v2 assembler.

    A supervisor must still perform live admission/current-token checks before
    advertising this pack as the current editable output. No vendor is invoked.
    """
    if not isinstance(bundle_root, Path) or not isinstance(output_root, Path) or not isinstance(expected_current_token, Mapping):
        raise ImportPackError("Review pack requires explicit paths and complete expected token")
    root, output = bundle_root.absolute(), output_root.absolute()
    _plain(root)
    _plain(output)
    if root.resolve().is_relative_to(output.resolve()) or output.resolve().is_relative_to(root.resolve()):
        raise ImportPackError("Review pack output overlaps its immutable bundle")
    deadline = time.monotonic() + 720
    raw, value, pins = _admit(root, expected_manifest_hash, expected_current_token, deadline)

    def resource(role):
        pin = pins[role]
        return root / pin["relative_path"]

    timeline = _load(_bytes(resource("corrected_timeline"), pins["corrected_timeline"]["sha256"],
                            _decimal(pins["corrected_timeline"]["size_bytes"], positive=True), deadline, capture=True))
    if type(timeline.get("schema_version")) is not int or timeline["schema_version"] != 1 or type(timeline.get("cues")) is not list or not 1 <= len(timeline["cues"]) <= 10000:
        raise ImportPackError("Review canonical timeline is incomplete")
    qc = _load(_bytes(resource("qc_report"), pins["qc_report"]["sha256"],
                      _decimal(pins["qc_report"]["size_bytes"], positive=True), deadline, capture=True))
    if type(qc.get("schema_version")) is not int or qc["schema_version"] != 1 or qc.get("status") != "passed":
        raise ImportPackError("Review bundle QC has not passed")
    fields = {"cue_id", "start_ticks", "end_ticks", "start_ms", "end_ms", "asset_ids", "track_id", "speaker_id", "voice_id"}
    if any(type(cue) is not dict for cue in timeline["cues"]):
        raise ImportPackError("Review canonical cue is invalid")
    mapping = {"time_base": timeline.get("time_base"), "duration_ticks": timeline.get("duration_ticks"),
               "cues": [{key: item for key, item in cue.items() if key in fields} for cue in timeline["cues"]]}
    definitions = (("corrected_timeline", "canonical-timeline", "timeline", "json"),
                   ("final_video", "localized-video", "video", "mp4"),
                   ("captions_srt", "vietsub-srt", "subtitle", "srt"),
                   ("captions_ass", "vietsub-ass", "subtitle", "ass"),
                   ("qc_report", "qc-report", "metadata", "json"),
                   ("original_audio", "source-audio", "original_audio", "wav"),
                   ("dialogue_stem", "dialogue-stem", "dub_audio", "wav"),
                   ("final_mix", "final-mix", "mixed_audio", "wav"))
    extras = tuple(ImportPackAsset(asset_id, kind, resource(role), format_name, pins[role]["sha256"])
                   for role, asset_id, kind, format_name in definitions if role in pins)
    extras += (ImportPackAsset("review-bundle-manifest", "metadata", root / _MANIFEST, "json", expected_manifest_hash),)
    result = create_production_pack(pack_id="review-" + value["generation"], source_path=resource("source_video"),
        output_root=output, timeline=mapping, additional_assets=extras, source_content_hash=pins["source_video"]["sha256"],
        storage_guard=storage_guard)
    # Rehash all input bytes after assembly and independently check every copied
    # resource. A late refusal leaves the complete pack for explicit recovery.
    fresh_raw, fresh, _ = _admit(root, expected_manifest_hash, expected_current_token, deadline)
    if fresh_raw != raw or fresh != value:
        raise ImportPackError("Review bundle changed while assembling its editable pack")
    checked = validate_import_pack(result.pack.root)
    expected = {"source-video": pins["source_video"]["sha256"], "review-bundle-manifest": expected_manifest_hash}
    expected.update({asset_id: pins[role]["sha256"] for role, asset_id, _, _ in definitions if role in pins})
    if checked["schema_version"] != 2 or {asset["asset_id"]: asset["content_hash"] for asset in checked["assets"]} != expected:
        raise ImportPackError("Review editable pack differs from its complete resource pins")
    return ReviewPackResult(result, expected_manifest_hash, dict(value["current_token"]), value["generation"])

"""Pinned offline Argos routes and private verified model installation."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import zipfile

from engine.dubflow.models import ensure_model_profile
from .adapter import TranslationError

PROFILE_PATH = "models/manifests/production-translation-v1.json"
RUNTIME_VERSION = "1.11.0"
COMPONENT_VERSIONS = {"ctranslate2": "4.8.2", "sentencepiece": "0.2.2"}
MAX_FILES = 512
MAX_EXPANDED_BYTES = 512 * 1024 * 1024


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plain(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise TranslationError("TRANSLATION_PATH_UNSAFE", "model paths cannot contain links")


def child(root: Path, name: str) -> Path:
    if type(name) is not str or re.search(r'[\\<>:"|?*\x00-\x1f\x7f]', name) or PurePosixPath(name).is_absolute():
        raise TranslationError("TRANSLATION_PATH_UNSAFE", "package path must be portable and relative")
    parts = name.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(not part or part in {".", ".."} or part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in parts):
        raise TranslationError("TRANSLATION_PATH_UNSAFE", "package path escapes or is unsafe on Windows")
    path = root.joinpath(*parts)
    plain(path)
    path.resolve().relative_to(root.resolve())
    return path


def read_json(path: Path, *, limit: int = 65536) -> dict:
    plain(path)
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise TranslationError("TRANSLATION_PROFILE_INVALID", "model metadata exceeds its bound")
    value = json.loads(payload)
    if type(value) is not dict:
        raise TranslationError("TRANSLATION_PROFILE_INVALID", "model metadata must be an object")
    return value


class CueSentencizer:
    """Deterministic cue punctuation recipe; never loads an auxiliary model."""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def split_sentences(self, text: str) -> list[str]:
        sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])|(?<=\.)\s+|\n+", text) if part.strip()]
        if not sentences or len(text) > 16384 or len(sentences) > 128:
            raise TranslationError("TRANSLATION_INPUT_TOO_LONG", "cue exceeds the bounded sentence recipe")
        if any(len(self.tokenizer.encode(part)) > 512 for part in sentences):
            raise TranslationError("TRANSLATION_INPUT_TOO_LONG", "sentence exceeds the safe inference token bound")
        return sentences


def tree_hash(root: Path, package: dict) -> str:
    folder = child(root, package["zip_root"])
    records = {}
    total = 0
    entries = 0
    for parent, dirs, files in os.walk(folder, followlinks=False):
        entries += len(dirs) + len(files)
        if entries > MAX_FILES:
            raise TranslationError("TRANSLATION_PACKAGE_INVALID", "installed package exceeds entry bounds")
        for name in dirs:
            plain(Path(parent) / name)
        for name in files:
            path = Path(parent) / name
            plain(path)
            size = path.stat().st_size
            total += size
            if len(records) >= MAX_FILES or total > MAX_EXPANDED_BYTES:
                raise TranslationError("TRANSLATION_PACKAGE_INVALID", "installed package exceeds resource bounds")
            records[path.relative_to(root).as_posix()] = {"sha256": digest(path), "size_bytes": size}
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def valid_package(root: Path, package: dict) -> bool:
    try:
        if tree_hash(root, package) != package["tree_sha256"]:
            return False
        metadata = read_json(child(root, package["zip_root"] + "/metadata.json"))
        return all(str(metadata.get(key)) == str(package[key]) for key in ("from_code", "to_code", "package_version"))
    except (OSError, ValueError, KeyError):
        return False


@contextmanager
def install_lock(root: Path):
    plain(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".language-install.lock"
    plain(path)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TranslationError("TRANSLATION_INSTALL_BUSY", "another worker is provisioning the route")
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def install_packages(destination: Path, cache: Path, packages: list[dict]) -> None:
    plain(destination.parent)
    temporary = Path(tempfile.mkdtemp(prefix=".argos-install-", dir=destination.parent))
    try:
        for package in packages:
            archive = child(cache, package["path"])
            if archive.stat().st_size != package["size_bytes"] or digest(archive) != package["sha256"]:
                raise TranslationError("TRANSLATION_PACKAGE_CHECKSUM_MISMATCH", "package archive differs from its pin")
            total = 0
            seen = set()
            with zipfile.ZipFile(archive) as reader:
                if len(reader.infolist()) > MAX_FILES:
                    raise TranslationError("TRANSLATION_PACKAGE_INVALID", "archive exceeds entry bounds")
                for entry in reader.infolist():
                    if not entry.filename.startswith(package["zip_root"] + "/"):
                        raise TranslationError("TRANSLATION_PATH_UNSAFE", "package has an unexpected root")
                    name = entry.filename.rstrip("/") if entry.is_dir() else entry.filename
                    target = child(temporary, name)
                    kind = stat.S_IFMT(entry.external_attr >> 16)
                    if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (kind == stat.S_IFDIR) != entry.is_dir() and kind != 0:
                        raise TranslationError("TRANSLATION_PATH_UNSAFE", "package includes links or special files")
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    total += entry.file_size
                    if name in seen or len(seen) >= MAX_FILES or total > MAX_EXPANDED_BYTES:
                        raise TranslationError("TRANSLATION_PACKAGE_INVALID", "package duplicates data or exceeds bounds")
                    seen.add(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with reader.open(entry) as source, target.open("xb") as output:
                        remaining = entry.file_size
                        while remaining:
                            block = source.read(min(remaining, 1024 * 1024))
                            if not block:
                                raise TranslationError("TRANSLATION_PACKAGE_INVALID", "package member is truncated")
                            output.write(block)
                            remaining -= len(block)
                        if source.read(1):
                            raise TranslationError("TRANSLATION_PACKAGE_INVALID", "package member exceeds its declared bound")
            if not valid_package(temporary, package):
                raise TranslationError("TRANSLATION_PACKAGE_CHECKSUM_MISMATCH", "extracted model differs from its pin")
        if destination.exists():
            plain(destination)
            os.replace(destination, destination.with_name(destination.name + ".corrupt-" + str(time.time_ns())))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            if temporary.resolve().parent != destination.parent.resolve() or temporary.is_symlink():
                raise TranslationError("TRANSLATION_PATH_UNSAFE", "refusing cleanup outside model staging")
            shutil.rmtree(temporary)


class ArgosRuntime:
    def __init__(self, app_root: Path, model_root: Path, source_language: str, *, progress=None):
        self.profile_path = child(app_root.absolute(), PROFILE_PATH)
        try:
            self.profile = read_json(self.profile_path)
        except FileNotFoundError as error:
            raise TranslationError("TRANSLATION_PROFILE_MISSING", "app-owned translation profile is missing") from error
        except (OSError, ValueError) as error:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "unable to read app-owned translation profile") from error
        if type(self.profile.get("schema_version")) is not int or self.profile.get("schema_version") != 1 or self.profile.get("runtime_version") != RUNTIME_VERSION or self.profile.get("runtime_components") != COMPONENT_VERSIONS:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "unsupported pinned translation profile")
        routes = self.profile.get("routes", {})
        if type(routes) is not dict or type(self.profile.get("artifacts")) is not list:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "routes and artifacts must be explicit collections")
        if source_language not in routes:
            raise TranslationError("TRANSLATION_ROUTE_UNAVAILABLE", "no compatible pinned source-to-Vietnamese route")
        self.source_language = source_language
        self.profile_hash = digest(self.profile_path)
        try:
            artifacts = self.profile["artifacts"]
            if not all(type(item) is dict for item in artifacts):
                raise ValueError("invalid artifacts")
            by_id = {item["id"]: item for item in artifacts}
            route = routes[source_language]
            if type(route) is not list or len(route) > 4 or len(by_id) != len(artifacts):
                raise ValueError("invalid route or duplicate artifact IDs")
            self.packages = [by_id[key] for key in route]
            language = "zh" if source_language in {"zh-CN", "zh-TW"} else source_language
            for item in self.packages:
                if item["from_code"] != language or type(item["to_code"]) is not str or not item["to_code"]:
                    raise ValueError("discontinuous language route")
                language = item["to_code"]
                if not all(type(item[key]) is str and re.fullmatch(r"[0-9a-f]{64}", item[key]) for key in ("sha256", "tree_sha256")) or type(item["size_bytes"]) is not int or not 0 < item["size_bytes"] <= MAX_EXPANDED_BYTES:
                    raise ValueError("invalid package pins")
                if type(item["package_version"]) is not str or not item["package_version"]:
                    raise ValueError("invalid package version")
            if language != "vi" or len({item["zip_root"] for item in self.packages}) != len(self.packages):
                raise ValueError("route does not terminate uniquely in Vietnamese")
        except (KeyError, TypeError, ValueError) as error:
            raise TranslationError("TRANSLATION_PROFILE_INVALID", "invalid pinned language route or package metadata") from error
        store_hash = hashlib.sha256((self.profile_hash + json.dumps(routes[source_language])).encode()).hexdigest()
        self.root = model_root.absolute() / "translation" / "installed_language_v1" / store_hash
        self.progress = progress
        self._translators = None

    def provenance(self) -> dict:
        return {"backend": "argos-pinned-routes-v1" if self.packages else "vi-identity-v1", "runtime": "argostranslate-" + RUNTIME_VERSION if self.packages else "none", "components": dict(COMPONENT_VERSIONS) if self.packages else {}, "requested_source_language": self.source_language, "backend_route": [self.packages[0]["from_code"], *(item["to_code"] for item in self.packages)] if self.packages else ["vi"], "profile_sha256": self.profile_hash, "settings": {"device": "cpu", "compute_type": "int8", "sentence_boundary": "cue-punctuation-v1", "beam_size": 4} if self.packages else {}, "packages": [{key: item[key] for key in ("id", "from_code", "to_code", "package_version", "sha256", "tree_sha256")} for item in self.packages]}

    def prepare(self, model_root: Path) -> None:
        if not self.packages:
            return
        try:
            for name, version in {"argostranslate": RUNTIME_VERSION, **COMPONENT_VERSIONS}.items():
                if importlib.metadata.version(name) != version:
                    raise TranslationError("TRANSLATION_RUNTIME_MISMATCH", "translation runtime differs from its component pins")
        except importlib.metadata.PackageNotFoundError as error:
            raise TranslationError("TRANSLATION_RUNTIME_MISSING", "app-owned translation component is missing") from error
        with install_lock(self.root.parent):
            ensure_model_profile(self.profile_path, model_root, progress=self.progress)
            if not all(valid_package(self.root, item) for item in self.packages):
                install_packages(self.root, model_root, self.packages)
            if set(path.name for path in self.root.iterdir()) != {item["zip_root"] for item in self.packages}:
                raise TranslationError("TRANSLATION_PACKAGE_INVALID", "route store contains unregistered packages")
        os.environ["ARGOS_PACKAGES_DIR"] = str(self.root)
        os.environ["ARGOS_TRANSLATE_PACKAGE_DIR"] = str(self.root)
        # Workers are private processes. Fence all SDK stores and user settings
        # before importing Argos; do not consult an account package/index cache.
        private = self.root.parent / (self.root.name + "-runtime")
        plain(private)
        for name in ("data", "config", "cache"):
            location = private / name
            plain(location)
            location.mkdir(parents=True, exist_ok=True)
            os.environ["XDG_" + name.upper() + "_HOME"] = str(location)
        for key, value in {"ARGOS_MODEL_PROVIDER": "OPENNMT", "ARGOS_DEVICE_TYPE": "cpu", "ARGOS_COMPUTE_TYPE": "int8", "ARGOS_CHUNK_TYPE": "STANZA", "ARGOS_BEAM_SIZE": "4", "ARGOS_BATCH_SIZE": "32", "ARGOS_INTER_THREADS": "1", "ARGOS_INTRA_THREADS": "4", "ARGOS_DEBUG": "0", "ARGOS_DEV_MODE": "0"}.items():
            os.environ[key] = value
        from argostranslate import settings, package, translate
        if Path(settings.package_data_dir).resolve() != self.root.resolve():
            raise TranslationError("TRANSLATION_CACHE_UNSAFE", "Argos resolves a different account/model store")
        if any(Path(getattr(settings, key)).resolve() != (private / name / "argos-translate").resolve() for key, name in (("data_dir", "data"), ("config_dir", "config"), ("cache_dir", "cache"))):
            raise TranslationError("TRANSLATION_CACHE_UNSAFE", "Argos resolves an external auxiliary store")
        if settings.device != "cpu" or settings.compute_type != "int8" or settings.chunk_type != settings.ChunkType.STANZA or settings.model_provider != settings.ModelProvider.OPENNMT:
            raise TranslationError("TRANSLATION_RUNTIME_MISMATCH", "Argos settings were initialized outside the pinned recipe")
        installed = package.get_installed_packages()
        expected = {(item["from_code"], item["to_code"], item["package_version"]) for item in self.packages}
        actual = {(item.from_code, item.to_code, str(item.package_version)) for item in installed}
        if actual != expected:
            raise TranslationError("TRANSLATION_PACKAGE_MISMATCH", "installed language/version pairs differ")
        self._translators = []
        for item in self.packages:
            pkg = next(value for value in installed if (value.from_code, value.to_code) == (item["from_code"], item["to_code"]))
            backend = translate.PackageTranslation(translate.Language(pkg.from_code, pkg.from_code), translate.Language(pkg.to_code, pkg.to_code), pkg)
            backend.sentencizer = CueSentencizer(pkg.tokenizer)
            self._translators.append(backend)

    def translate_text(self, text: str) -> str:
        if not self.packages:
            return text
        if self._translators is None:
            raise TranslationError("TRANSLATION_RUNTIME_MISSING", "route has not been initialized")
        value = text
        for backend in self._translators:
            value = backend.translate(value)
            if type(value) is not str or not value.strip():
                raise TranslationError("TRANSLATION_EMPTY", "pinned route produced no translated text")
        return " ".join(value.split())

"""Read-only, bounded Windows registration discovery for optional CapCut handoff.

Registration is evidence of a registered installation, not a tested draft format
or proof that its executable is usable. No discovered path/command is executed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import re
import sys
from typing import Any


_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
_NAMES = frozenset({"capcut", "capcut desktop", "剪映专业版", "jianyingpro"})
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,9})(?:\.(?:0|[1-9][0-9]{0,9})){2,3}\Z")
_MAX_KEYS_PER_VIEW = 512
_MAX_REGISTRATIONS = 32


class DiscoveryStatus(str, Enum):
    REGISTERED = "REGISTERED"
    NOT_REGISTERED = "NOT_REGISTERED"
    UNKNOWN_VERSION = "UNKNOWN_VERSION"
    AMBIGUOUS = "AMBIGUOUS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INCOMPLETE = "INCOMPLETE"
    PLATFORM_UNSUPPORTED = "PLATFORM_UNSUPPORTED"


@dataclass(frozen=True)
class CapCutRegistration:
    display_name: str
    raw_version: str | None
    version: str | None
    hive: str
    view: str
    key: str


@dataclass(frozen=True)
class CapCutDiscovery:
    status: DiscoveryStatus
    registrations: tuple[CapCutRegistration, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def version(self) -> str | None:
        # Partial/ambiguous scans never authorize a guessed version.
        return self.registrations[0].version if self.status == DiscoveryStatus.REGISTERED else None

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "adapter_version": "windows-uninstall-registry-v1",
                "status": self.status.value, "version": self.version,
                "registrations": [asdict(item) for item in self.registrations],
                "issues": list(self.issues),
                "scope": "current-user and machine uninstall registration, 32/64-bit views; no executable or draft qualification"}


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
        return None
    return value.strip() or None


def discover_capcut_installation(*, registry: Any = None, platform: str | None = None) -> CapCutDiscovery:
    """Read local uninstall metadata with KEY_READ and close every key handle.

    Missing roots/values are normal. Denied access, registry changes, read errors
    and enumeration limits are data; they prevent selecting a direct version.
    Registry/platform injection is for deterministic boundary tests only.
    """
    if (platform if platform is not None else sys.platform) != "win32":
        return CapCutDiscovery(DiscoveryStatus.PLATFORM_UNSUPPORTED)
    if registry is None:
        try:
            import winreg as registry
        except ImportError:
            return CapCutDiscovery(DiscoveryStatus.INCOMPLETE, issues=("REGISTRY_ADAPTER_UNAVAILABLE",))
    registrations: list[CapCutRegistration] = []
    issues: set[str] = set()
    seen: set[tuple[str, str, str, str | None]] = set()

    def failure(error: OSError, scope: str) -> None:
        denied = isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5
        issues.add(scope + (":PERMISSION_DENIED" if denied else ":READ_FAILED"))

    def value(key: Any, name: str, scope: str, maximum: int) -> str | None:
        try:
            data, kind = registry.QueryValueEx(key, name)
            return _text(data, maximum) if kind in (registry.REG_SZ, registry.REG_EXPAND_SZ) else None
        except FileNotFoundError:
            return None
        except OSError as error:
            failure(error, scope)
            return None

    for hive_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        for view_name, flag in (("64", registry.KEY_WOW64_64KEY), ("32", registry.KEY_WOW64_32KEY)):
            scope = hive_name + ":" + view_name
            try:
                with registry.OpenKey(getattr(registry, hive_name), _UNINSTALL_KEY, 0, registry.KEY_READ | flag) as root:
                    before = registry.QueryInfoKey(root)
                    for index in range(_MAX_KEYS_PER_VIEW + 1):
                        try:
                            child = registry.EnumKey(root, index)
                        except OSError as error:
                            if getattr(error, "winerror", None) != 259:  # ERROR_NO_MORE_ITEMS only
                                failure(error, scope)
                            break
                        if index == _MAX_KEYS_PER_VIEW:
                            issues.add(scope + ":ENUMERATION_LIMIT")
                            break
                        if not _text(child, 256) or "\\" in child or "/" in child:
                            issues.add(scope + ":INVALID_KEY")
                            continue
                        try:
                            with registry.OpenKey(root, child, 0, registry.KEY_READ | flag) as entry:
                                name = value(entry, "DisplayName", scope, 256)
                                if name is None or name.casefold() not in _NAMES:
                                    continue
                                raw = value(entry, "DisplayVersion", scope, 128)
                                identity = (hive_name, child, name.casefold(), raw)
                                if identity in seen:
                                    continue  # Shared HKCU view must not invent two installations.
                                seen.add(identity)
                                if len(registrations) == _MAX_REGISTRATIONS:
                                    issues.add(scope + ":REGISTRATION_LIMIT")
                                    continue
                                version = raw if raw is not None and _VERSION.fullmatch(raw) else None
                                registrations.append(CapCutRegistration(name, raw, version, hive_name, view_name, child))
                        except FileNotFoundError:
                            issues.add(scope + ":REGISTRY_CHANGED")
                        except OSError as error:
                            failure(error, scope)
                    if registry.QueryInfoKey(root) != before:
                        issues.add(scope + ":REGISTRY_CHANGED")
            except FileNotFoundError:
                continue
            except OSError as error:
                failure(error, scope)
    if issues:
        status = DiscoveryStatus.PERMISSION_DENIED if any(item.endswith(":PERMISSION_DENIED") for item in issues) else DiscoveryStatus.INCOMPLETE
    elif len(registrations) > 1:
        status = DiscoveryStatus.AMBIGUOUS
    elif not registrations:
        status = DiscoveryStatus.NOT_REGISTERED
    elif registrations[0].version is None:
        status = DiscoveryStatus.UNKNOWN_VERSION
    else:
        status = DiscoveryStatus.REGISTERED
    return CapCutDiscovery(status, tuple(registrations), tuple(sorted(issues)))

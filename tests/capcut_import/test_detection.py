from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.dubflow.capcut.detection import DiscoveryStatus, discover_capcut_installation
from engine.dubflow.capcut.production import create_production_pack, detect_capcut_version
from engine.dubflow.capcut.import_pack import validate_import_pack


def winerror(code: int) -> OSError:
    error = OSError("controlled registry boundary")
    error.winerror = code
    return error


class Key:
    def __init__(self, registry, hive, view, entry=None):
        self.registry, self.hive, self.view, self.entry = registry, hive, view, entry

    def __enter__(self):
        self.registry.open_count += 1
        return self

    def __exit__(self, *args):
        self.registry.open_count -= 1


class Registry:
    HKEY_CURRENT_USER = "user"
    HKEY_LOCAL_MACHINE = "machine"
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 1, 256, 512
    REG_SZ, REG_EXPAND_SZ = 1, 2

    def __init__(self):
        self.entries = {}
        self.denied = set()
        self.open_count = 0
        self.access = []
        self.queries = []
        self.enum_calls = 0
        self.changed = False
        self.info_calls = 0

    def add(self, key="CapCut", version="4.2.0", *, hive="user", view=256, name="CapCut"):
        self.entries.setdefault((hive, view), {})[key] = {"DisplayName": name, "DisplayVersion": version}

    def OpenKey(self, hive, path, reserved, access):
        self.access.append(access)
        view = access & ~self.KEY_READ
        if isinstance(hive, Key):
            if (hive.hive, view, path) in self.denied:
                raise PermissionError("controlled denied subkey")
            return Key(self, hive.hive, view, path)
        if (hive, view) in self.denied:
            raise PermissionError("controlled denied root")
        if (hive, view) not in self.entries:
            raise FileNotFoundError(path)
        return Key(self, hive, view)

    def QueryInfoKey(self, key):
        self.info_calls += 1
        stamp = self.info_calls if self.changed else 0
        return len(self.entries[(key.hive, key.view)]), 0, stamp

    def EnumKey(self, key, index):
        self.enum_calls += 1
        try:
            return list(self.entries[(key.hive, key.view)])[index]
        except IndexError:
            raise winerror(259)

    def QueryValueEx(self, key, name):
        self.queries.append(name)
        entry = self.entries[(key.hive, key.view)][key.entry]
        if name not in entry:
            raise FileNotFoundError(name)
        data = entry[name]
        if isinstance(data, Exception):
            raise data
        return (data, self.REG_SZ)


class CapCutDiscoveryTests(unittest.TestCase):
    def discover(self, registry):
        result = discover_capcut_installation(registry=registry, platform="win32")
        self.assertEqual(registry.open_count, 0)
        self.assertTrue(all(access & 1 for access in registry.access))
        self.assertLessEqual(set(registry.queries), {"DisplayName", "DisplayVersion"})
        return result

    def test_four_component_build_is_not_coerced_to_tested_three_component_version(self):
        registry = Registry()
        registry.add(version="4.2.0.1234")
        result = self.discover(registry)
        self.assertEqual(result.version, "4.2.0.1234")
        with tempfile.TemporaryDirectory(prefix="capcut-discovery-") as directory:
            root = Path(directory)
            source = root / "source with spaces.mp4"
            source.write_bytes(b"controlled media fixture")
            pack = create_production_pack(pack_id="version-build", source_path=source,
                output_root=root / "portable pack", installed_capcut_version=result.version,
                supported_capcut_versions={"4.2.0"}, controlled_capcut_root=root / "chosen directory")
            self.assertEqual(pack.direct_draft.reason, "CAPCUT_VERSION_UNSUPPORTED")
            self.assertFalse((root / "chosen directory").exists())
            self.assertEqual(source.read_bytes(), b"controlled media fixture")

    def test_32_bit_machine_registration_and_unicode_name(self):
        registry = Registry()
        registry.add(hive="machine", view=512, name="剪映专业版")
        result = self.discover(registry)
        self.assertEqual(result.status, DiscoveryStatus.REGISTERED)
        self.assertEqual(result.registrations[0].view, "32")
        self.assertEqual(result.to_dict()["adapter_version"], "windows-uninstall-registry-v1")

    def test_shared_user_views_are_deduplicated(self):
        registry = Registry()
        registry.add()
        registry.add(view=512)
        self.assertEqual(len(self.discover(registry).registrations), 1)

    def test_two_distinct_installs_do_not_select_even_identical_versions(self):
        registry = Registry()
        registry.add()
        registry.add(hive="machine")
        result = self.discover(registry)
        self.assertEqual(result.status, DiscoveryStatus.AMBIGUOUS)
        self.assertIsNone(result.version)

    def test_missing_metadata_and_future_versions_remain_unknown(self):
        for version in (None, "", "preview-2027", "4.2.0\x00", 42, "9" * 129):
            with self.subTest(version=version):
                registry = Registry()
                registry.add(version=version)
                result = self.discover(registry)
                self.assertEqual(result.status, DiscoveryStatus.UNKNOWN_VERSION)
                self.assertIsNone(result.version)

    def test_absent_roots_and_similar_unrecognized_names_are_registration_absence_only(self):
        registry = Registry()
        registry.add(name="CapCut helper malware")
        result = self.discover(registry)
        self.assertEqual(result.status, DiscoveryStatus.NOT_REGISTERED)
        self.assertIn("no executable", result.to_dict()["scope"])

    def test_denied_other_view_prevents_known_version_selection(self):
        registry = Registry()
        registry.add()
        registry.denied.add(("machine", 512))
        result = self.discover(registry)
        self.assertEqual(result.status, DiscoveryStatus.PERMISSION_DENIED)
        self.assertEqual(len(result.registrations), 1)
        self.assertIsNone(result.version)

    def test_denied_entry_and_value_are_not_reported_as_absence(self):
        for level in ("entry", "value"):
            with self.subTest(level=level):
                registry = Registry()
                registry.add()
                if level == "entry":
                    registry.denied.add(("user", 256, "CapCut"))
                else:
                    registry.entries[("user", 256)]["CapCut"]["DisplayVersion"] = PermissionError()
                result = self.discover(registry)
                self.assertEqual(result.status, DiscoveryStatus.PERMISSION_DENIED)
                self.assertIsNone(result.version)

    def test_non_terminal_enumeration_error_is_incomplete(self):
        registry = Registry()
        registry.add()
        with patch.object(registry, "EnumKey", side_effect=winerror(1018)):
            self.assertEqual(self.discover(registry).status, DiscoveryStatus.INCOMPLETE)

    def test_registry_change_prevents_version_selection(self):
        registry = Registry()
        registry.add()
        registry.changed = True
        self.assertIsNone(self.discover(registry).version)

    def test_enumeration_and_registration_limits_do_not_imply_absence(self):
        registry = Registry()
        for index in range(513):
            registry.add(key=str(index), name="other product")
        result = self.discover(registry)
        self.assertEqual(result.status, DiscoveryStatus.INCOMPLETE)
        self.assertEqual(registry.enum_calls, 513)
        registry = Registry()
        for index in range(33):
            registry.add(key=str(index))
        result = self.discover(registry)
        self.assertEqual(len(result.registrations), 32)
        self.assertEqual(result.status, DiscoveryStatus.INCOMPLETE)

    def test_normal_detection_ignores_ambient_version_override(self):
        registry = Registry()
        with patch.dict(os.environ, {"DUBFLOW_CAPCUT_VERSION": "4.2.0"}), patch(
                "engine.dubflow.capcut.production.discover_capcut_installation",
                return_value=self.discover(registry)):
            self.assertIsNone(detect_capcut_version())
            self.assertEqual(detect_capcut_version({"DUBFLOW_CAPCUT_VERSION": "4.2.0.1234"}), "4.2.0.1234")

    def test_unsupported_platform_does_not_probe_registry(self):
        registry = Registry()
        result = discover_capcut_installation(registry=registry, platform="linux")
        self.assertEqual(result.status, DiscoveryStatus.PLATFORM_UNSUPPORTED)
        self.assertEqual(registry.access, [])

    def test_optional_directory_denial_retains_valid_pack_and_source(self):
        with tempfile.TemporaryDirectory(prefix="capcut-denied-") as directory:
            root = Path(directory)
            source, chosen = root / "source.mp4", root / "chosen directory"
            source.write_bytes(b"canonical media retained")
            original_mkdir = Path.mkdir

            def mkdir(path, *args, **kwargs):
                if path == chosen:
                    raise PermissionError("controlled user-selected directory denied")
                return original_mkdir(path, *args, **kwargs)

            with patch.object(Path, "mkdir", mkdir):
                pack = create_production_pack(pack_id="denied-draft", source_path=source,
                    output_root=root / "portable pack", installed_capcut_version="4.2.0",
                    supported_capcut_versions={"4.2.0"}, controlled_capcut_root=chosen)
            self.assertEqual(pack.direct_draft.reason, "CAPCUT_HANDOFF_FAILED:PermissionError")
            self.assertEqual(validate_import_pack(pack.pack.root), pack.pack.manifest)
            self.assertEqual(source.read_bytes(), b"canonical media retained")
            self.assertFalse(chosen.exists())

    def test_broken_optional_matrix_retains_valid_pack(self):
        with tempfile.TemporaryDirectory(prefix="capcut-matrix-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"canonical media retained")
            pack = create_production_pack(pack_id="broken-matrix", source_path=source,
                output_root=root / "portable pack", installed_capcut_version="4.2.0",
                supported_capcut_versions={"malformed"}, controlled_capcut_root=root / "chosen directory")
            self.assertEqual(pack.direct_draft.reason, "CAPCUT_HANDOFF_FAILED:DirectDraftError")
            self.assertEqual(validate_import_pack(pack.pack.root), pack.pack.manifest)
            self.assertFalse((root / "chosen directory").exists())

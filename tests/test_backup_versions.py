# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise real backup archives across the 1.3.0 to 1.4.0 upgrade."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


PLUGIN = Path(__file__).resolve().parents[1] / "GTIPTVPlayerPro"
SPEC = importlib.util.spec_from_file_location(
    "gt_backup_release_test", PLUGIN / "__init__.py",
    submodule_search_locations=[str(PLUGIN)],
)
PACKAGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PACKAGE
SPEC.loader.exec_module(PACKAGE)
from gt_backup_release_test import backup


class BackupReleaseVersions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.logical = "/etc/enigma2/gtiptvplayerpro-favorites.json"
        self.payload = b'{"version":1,"items":{},"order":[]}'
        source = self.source / self.logical.lstrip("/")
        source.parent.mkdir(parents=True)
        source.write_bytes(self.payload)
        self.folder = self.source / "archives"
        self.folder.mkdir()
        self.restore_folder = self.target / "archives"
        self.restore_folder.mkdir(parents=True)
        self.current = self.target / self.logical.lstrip("/")
        self.current.parent.mkdir(parents=True)
        self.current.write_bytes(b'{"preserved":true}')

    def archive(self, version):
        with patch.object(backup, "PACKAGE_VERSION", version):
            result = backup.BackupManager(root=str(self.source)).create(
                str(self.folder), groups=("favorites",),
            )
        archive = Path(result["path"])
        copied = self.restore_folder / archive.name
        copied.write_bytes(archive.read_bytes())
        return str(copied)

    def restore(self, archive):
        return backup.BackupManager(root=str(self.target)).restore(
            archive, safety_folder=str(self.restore_folder), groups=("favorites",),
        )

    def test_current_release_round_trip_and_safety_copy(self):
        archive = self.archive(backup.PACKAGE_VERSION)
        with zipfile.ZipFile(archive) as handle:
            self.assertEqual(json.loads(handle.read("manifest.json"))["package_version"], "1.4.0-r0")
        result = self.restore(archive)
        self.assertEqual(self.current.read_bytes(), self.payload)
        self.assertTrue(result)
        safety = list(self.restore_folder.glob("*safety*.gtbackup"))
        self.assertEqual(len(safety), 1)
        with zipfile.ZipFile(safety[0]) as handle:
            manifest = json.loads(handle.read("manifest.json"))
            self.assertEqual(manifest["package_version"], "1.4.0-r0")
            self.assertEqual(handle.read(manifest["files"][0]["member"]), b'{"preserved":true}')

    def test_previous_1_3_0_archives_restore_on_1_4_0(self):
        for version in ("1.3.0", "1.3.0-r5", "1.3.0-r56"):
            with self.subTest(version=version):
                self.current.write_bytes(b'{"preserved":true}')
                self.restore(self.archive(version))
                self.assertEqual(self.current.read_bytes(), self.payload)

    def test_unsupported_versions_are_rejected_before_data_changes(self):
        for version in ("1.2.0-r0", "1.5.0-r0", "2.0.0-r0", "1.4.1-r0", "1.4.0-r0-extra"):
            with self.subTest(version=version):
                with self.assertRaises(backup.BackupError) as caught:
                    self.restore(self.archive(version))
                self.assertEqual(caught.exception.code, "version")
                self.assertEqual(self.current.read_bytes(), b'{"preserved":true}')

    def test_newer_archive_format_is_rejected_before_data_changes(self):
        original = self.archive(backup.PACKAGE_VERSION)
        changed = self.restore_folder / "future-format.gtbackup"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "manifest.json":
                    manifest = json.loads(data)
                    manifest["format_version"] = backup.FORMAT_VERSION + 1
                    data = json.dumps(manifest).encode()
                target.writestr(entry, data)
        with self.assertRaises(backup.BackupError) as caught:
            self.restore(str(changed))
        self.assertEqual(caught.exception.code, "invalid")
        self.assertEqual(self.current.read_bytes(), b'{"preserved":true}')


if __name__ == "__main__":
    unittest.main()

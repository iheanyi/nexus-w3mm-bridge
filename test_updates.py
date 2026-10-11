"""Real update/uninstall flows, isolated from the user's game and inventory."""
import os
import tempfile
import time
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import bridge
from bridge_core import BridgeError
from inventory import (InstalledMod, binding_for, from_model, inspect_install_archive,
                       missing_payloads, read_inventory, replaced_entries, save_library)
from src.core.installer import Installer


class UpdateRulesTests(unittest.TestCase):
    def test_only_fully_replaced_main_package_is_retired(self):
        old = InstalledMod('Old', True, 'date', (('data', 'modMain'), ('menu', 'main.xml')))
        new = InstalledMod('New', True, 'newdate', old.components + (('ini', 'main.ini'),))
        file = {'file_id': 1, 'category_id': 1}
        bindings = {old.key: binding_for(old, 7, file)}
        self.assertEqual(replaced_entries([old], new, bindings, 7, file), [old])
        for component in [('data', 'modExtra'), ('dlc', 'dlcExtra'), ('menu', 'optional.xml')]:
            extra = InstalledMod('Old bundle', True, 'date', old.components + (component,))
            tracking = {extra.key: binding_for(extra, 7, file)}
            self.assertEqual(replaced_entries([extra], new, tracking, 7, file, extra.key), [])

    def test_optional_different_page_disabled_and_changed_records_are_kept(self):
        old = InstalledMod('Old', True, 'date', (('data', 'modMain'),))
        new = InstalledMod('New', True, 'newdate', old.components)
        file = {'file_id': 1, 'category_id': 1}
        binding = binding_for(old, 7, file)
        cases = [dict(binding, category_id=3), dict(binding, mod_id=8), dict(binding, inventory_date='changed')]
        for wrong in cases:
            self.assertEqual(replaced_entries([old], new, {old.key: wrong}, 7, file, old.key), [])
        disabled = InstalledMod(old.name, False, old.date, old.components)
        self.assertEqual(replaced_entries([disabled], new, {old.key: binding}, 7, file, old.key), [])
        self.assertEqual(replaced_entries([old], new, {old.key: binding}, 7, dict(file, category_id=3), old.key), [])
        archived = {old.key: dict(binding, category_id=4)}
        self.assertEqual(replaced_entries([old], new, archived, 7, file), [])
        self.assertEqual(replaced_entries([old], new, archived, 7, file, old.key), [old])

    def test_missing_folders_are_detected_even_when_inventory_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            entry = InstalledMod('Main', True, 'date', (('data', 'modMain'),))
            self.assertEqual(missing_payloads(entry, game), ['modMain'])
            (game / 'Mods/modMain').mkdir(parents=True)
            self.assertEqual(missing_payloads(entry, game), [])
            (game / 'Mods/modMain').rename(game / 'Mods/~modMain')
            self.assertEqual(missing_payloads(entry, game), ['modMain'])
            disabled = InstalledMod(entry.name, False, entry.date, entry.components)
            self.assertEqual(missing_payloads(disabled, game), [])


class UpdateIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            game, docs, config = folder / 'game', folder / 'docs', folder / 'manager'
            exe = game / 'bin/x64_dx12/witcher3.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            (game / 'content').mkdir()
            docs.mkdir()
            config.mkdir()
            (config / 'installed.xml').write_text('<installed/>')
            (config / 'config.ini').write_text(
                f'[PATHS]\ngameexe={exe.as_posix()}\ndocuments={docs.as_posix()}\n'
                '[SETTINGS]\nallowpopups=0\ntheme=Dark\nlanguage=English.qm\nusenativedialog=0\n[TOOLBAR]\n')
            with patch.object(bridge, 'USER_DIR', folder / 'private'), patch.object(bridge, 'running_game', return_value=False), \
                    patch.object(bridge, 'read_prefs', return_value={'manager_config_dir': str(config)}), \
                    patch.object(bridge.BridgeWindow, 'connect_account', lambda s: None), \
                    patch.object(bridge.Manager, 'show'):
                manager = bridge.Manager(self.app, config)
                window = bridge.BridgeWindow()
                window.manager = manager
                try:
                    yield folder, game, config, manager, window
                finally:
                    deadline = time.monotonic() + 10
                    while window.workers and time.monotonic() < deadline:
                        QTest.qWait(10)
                    window.sync_timer.stop()
                    manager.save()
                    manager.widget.modsSettingsWatcher.stop()
                    self.app.aboutToQuit.disconnect(manager.save)
                    self.app.aboutToQuit.disconnect(manager.widget.modsSettingsWatcher.stop)
                    manager.model.lock.release()
                    window.close()

    def archive(self, folder, name, version, *, roots=('modMain',), obsolete=False):
        archive = folder / name
        with zipfile.ZipFile(archive, 'w') as output:
            for root in roots:
                output.writestr(f'Mods/{root}/content/version.ws', version)
                if obsolete:
                    output.writestr(f'Mods/{root}/content/obsolete.ws', 'old')
        return archive

    def start_update(self, folder, manager, window):
        old_archive = self.archive(folder, 'Previous 1.zip', 'v1', obsolete=True)
        self.assertEqual(manager.install([str(old_archive)]), (1, 0, 0))
        window.sync_inventory(force=True)
        old = window.entries[0]
        file = {'file_id': 1, 'name': 'Main', 'version': '1', 'category_id': 1}
        window.bindings[old.key] = binding_for(old, 7, file)
        save_library(window.library_file, window.bindings)
        new_archive = self.archive(folder, 'Renamed release 2.zip', 'v2')
        window.archive = new_archive
        window.archive_source = (7, dict(file, file_id=2, version='2'))
        window.tracking_entry = old
        return old, inspect_install_archive(new_archive, bridge.ROOT / 'tools/7zip/7z.exe')

    def finish_workers(self, window):
        deadline = time.monotonic() + 15
        while window.workers and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window.workers, 'Verification did not finish')

    def test_verified_update_retires_record_but_keeps_new_files(self):
        with self.fixture() as (folder, game, config, manager, window):
            old, inspected = self.start_update(folder, manager, window)
            with patch('src.core.installer.MessageOverwrite', return_value=QMessageBox.StandardButton.Yes):
                window.install_prepared(inspected)
                self.finish_workers(window)
            self.assertEqual(len(read_inventory(config)), 1)
            self.assertNotEqual(read_inventory(config)[0].name, old.name)
            self.assertEqual((game / 'Mods/modMain/content/version.ws').read_text(), 'v2')
            self.assertFalse((game / 'Mods/modMain/content/obsolete.ws').exists())
            self.assertNotIn(old.key, window.bindings)
            self.assertEqual(next(iter(window.bindings.values()))['file_id'], 2)
            self.assertIn('Replaced 1', window.status.text())
            backups = list((folder / 'private/backups').glob('before-update-cleanup-*/installed.xml'))
            self.assertEqual(len(backups), 1)
            self.assertIn(old.name, backups[0].read_text())
            self.assertTrue(manager.window.isEnabled())

    def test_declined_update_keeps_old_files_record_and_tracking(self):
        with self.fixture() as (folder, game, config, manager, window):
            old, inspected = self.start_update(folder, manager, window)
            with patch('src.core.installer.MessageOverwrite', return_value=QMessageBox.StandardButton.No):
                window.install_prepared(inspected)
            self.assertEqual(read_inventory(config), [old])
            self.assertEqual((game / 'Mods/modMain/content/version.ws').read_text(), 'v1')
            self.assertEqual(window.bindings[old.key]['file_id'], 1)

    def test_failed_verification_does_not_retire_or_track_new_version(self):
        with self.fixture() as (folder, game, config, manager, window):
            old, inspected = self.start_update(folder, manager, window)
            with patch('src.core.installer.MessageOverwrite', return_value=QMessageBox.StandardButton.Yes), \
                    patch.object(bridge, 'archive_matches', return_value=False):
                window.install_prepared(inspected)
                self.finish_workers(window)
            self.assertIn(old, read_inventory(config))
            self.assertEqual(list(window.bindings), [old.key])
            self.assertIn('old entry was kept', window.status.text())

    def test_shared_payload_cannot_be_removed_by_uninstalling_old_record(self):
        with self.fixture() as (folder, game, config, manager, window):
            old, _ = self.start_update(folder, manager, window)
            with patch('src.core.installer.MessageOverwrite', return_value=QMessageBox.StandardButton.Yes):
                manager.install([str(window.archive)])
            messages = []
            self.assertFalse(Installer(manager.model, output=messages.append).uninstallMod(manager.model.get(old.name)))
            self.assertIn('No files removed', messages[0])
            self.assertEqual(len(read_inventory(config)), 2)
            self.assertEqual((game / 'Mods/modMain/content/version.ws').read_text(), 'v2')

    def test_failed_cleanup_save_restores_model_and_preserves_payload(self):
        with self.fixture() as (folder, game, config, manager, window):
            old, _ = self.start_update(folder, manager, window)
            with patch('src.core.installer.MessageOverwrite', return_value=QMessageBox.StandardButton.Yes):
                manager.install([str(window.archive)])
            new = next(e for e in from_model(manager.model) if e.name != old.name)
            with patch.object(manager.model, 'write', return_value=False), self.assertRaises(BridgeError):
                manager.retire_replaced([old], new, window.bindings, window.archive_source)
            self.assertIn(old, from_model(manager.model))
            self.assertEqual(len(read_inventory(config)), 2)
            self.assertEqual((game / 'Mods/modMain/content/version.ws').read_text(), 'v2')

    def test_partial_overwrite_does_not_claim_complete_install(self):
        for declined in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.NoButton):
            with self.subTest(declined=declined), self.fixture() as (folder, game, config, manager, window):
                first = self.archive(folder, 'Bundle 1.zip', 'v1', roots=('modA', 'modB'))
                manager.install([str(first)])
                previous = read_inventory(config)
                second = self.archive(folder, 'Bundle 2.zip', 'v2', roots=('modA', 'modB'))
                with patch('src.core.installer.MessageOverwrite', side_effect=[QMessageBox.StandardButton.Yes, declined]), \
                        patch('src.gui.main_widget.MessageAlertIncompleteInstallation'):
                    self.assertEqual(manager.install([str(second)]), (1, 0, 1))
                self.assertEqual(read_inventory(config), previous)


if __name__ == '__main__':
    unittest.main()

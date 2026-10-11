"""Slow/offline Nexus must not gate the cached inventory on first launch."""
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import bridge
from bridge_core import BridgeError
from inventory import binding_for, library_path, read_inventory, save_library


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), 'Background operation did not reach expected state')

    @contextmanager
    def fixture(self, *, tracked=True, failure=False):
        account_started, release_account, release_job = (threading.Event() for _ in range(3))
        calls = []
        def account(operation, **kwargs):
            calls.append((operation, kwargs))
            account_started.set()
            release_account.wait(3)
            if failure:
                raise BridgeError('Cannot reach Vortex')
            return {'authenticated': True, 'premium': True}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game, config, user = root / 'game', root / 'manager', root / 'user'
            (game / 'Mods/modMain').mkdir(parents=True)
            config.mkdir()
            (config / 'config.ini').write_text(f'[PATHS]\ngameexe={(game / "bin/x64/witcher3.exe").as_posix()}\n')
            (config / 'installed.xml').write_text('<installed><mod name="Main" enabled="True" date="one"><data>modMain</data></mod></installed>')
            entry = read_inventory(config)[0]
            file = {'file_id': 10, 'name': 'Main', 'version': '1', 'category_id': 1}
            if tracked:
                save_library(library_path(user, config), {entry.key: binding_for(entry, 1, file)})
            save_library(user / 'nexus-cache.json', {'1': [{'name': 'Main'}, [file]]})
            with patch.object(bridge, 'USER_DIR', user), \
                    patch.object(bridge, 'read_prefs', return_value={'manager_config_dir': str(config)}), \
                    patch.object(bridge.NexusBridge, 'rpc', side_effect=account):
                window = bridge.BridgeWindow(connect_on_start=False)
                try:
                    window.connect_account()
                    self.until(account_started.is_set)
                    yield window, release_account, release_job, calls
                finally:
                    release_account.set()
                    release_job.set()
                    self.until(lambda: not window.workers)
                    window.sync_timer.stop()
                    window.close()

    def test_cached_inventory_is_usable_while_account_check_is_pending(self):
        with self.fixture() as (window, release, _, calls):
            self.assertIsNone(window.job)
            self.assertIsNotNone(window.account_job)
            self.assertEqual(window.installed_table.item(0, 3).text(), 'Up to date')
            self.assertTrue(window.refresh_button.isEnabled())
            self.assertTrue(window.import_button.isEnabled())
            self.assertTrue(window.review_button.isEnabled())
            self.assertTrue(window.find.isEnabled())
            self.assertEqual(window.progress.maximum(), 100)
            with patch.object(window, 'load_link') as load:
                window.review_installed()
                load.assert_called_once_with(bridge.ModLink(1), tracking=True)
            self.assertEqual(calls, [('nexus_account', {'timeout': 5})])
            archive = Path('saved-download.zip')
            window.archive = archive
            release.set()
            self.until(lambda: window.account_job is None)
            self.assertEqual(window.archive, archive)

    def test_account_result_cannot_unlock_or_overwrite_foreground_work(self):
        with self.fixture() as (window, release, release_job, _):
            window.work(lambda _: release_job.wait(3), lambda _: None)
            foreground = window.job
            window.status.setText('Downloading chosen file')
            release.set()
            self.until(lambda: window.account_job is None)
            self.assertIs(window.job, foreground)
            self.assertFalse(window.find.isEnabled())
            self.assertFalse(window.review_button.isEnabled())
            self.assertEqual(window.status.text(), 'Downloading chosen file')

    def test_unlinked_inventory_does_not_trigger_startup_archive_scan(self):
        with self.fixture(tracked=False) as (window, release, _, _), \
                patch.object(window, 'match_archives') as scan:
            release.set()
            self.until(lambda: not window.workers)
            QTest.qWait(20)
            scan.assert_not_called()
            self.assertTrue(window.import_button.isEnabled())
            self.assertEqual(window.installed_table.rowCount(), 1)

    def test_offline_account_keeps_inventory_and_cached_status_available(self):
        with self.fixture(failure=True) as (window, release, _, _):
            release.set()
            self.until(lambda: not window.workers)
            self.assertIn('Nexus unavailable', window.connection.text())
            self.assertIn('Reconnect in Settings', window.connection.text())
            self.assertEqual(window.installed_table.item(0, 3).text(), 'Up to date')
            self.assertTrue(window.review_button.isEnabled())
            self.assertTrue(window.refresh_button.isEnabled())


if __name__ == '__main__':
    unittest.main()

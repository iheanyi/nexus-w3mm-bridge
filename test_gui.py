"""Qt integration checks against a disposable W3MM inventory, never the user's game."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
import bridge
from inventory import inspect_install_archive, read_inventory


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_external_inventory_refresh_and_file_categories(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'config.ini').write_text('[PATHS]\n')
            inventory = folder / 'installed.xml'
            inventory.write_text('<installed><mod name="Main" enabled="True" date="x"><data>modA</data></mod></installed>')
            with patch.object(bridge, 'USER_DIR', folder / 'user'), patch.object(bridge, 'read_prefs', return_value={'manager_config_dir': str(folder)}), patch.object(bridge.BridgeWindow, 'connect_account', lambda s: None):
                window = bridge.BridgeWindow()
                try:
                    self.assertEqual(window.installed_table.rowCount(), 1)
                    inventory.write_text('<installed><mod name="Optional" enabled="False" date="y"><data>modB</data></mod></installed>')
                    window.sync_inventory()
                    self.assertEqual(window.installed_table.item(0, 0).text(), 'Optional')
                    self.assertEqual(window.installed_table.item(0, 1).text(), 'Disabled')
                    inventory.write_text('<installed><mod')
                    window.sync_inventory()
                    self.assertEqual(window.installed_table.rowCount(), 1)
                    window.link = bridge.ModLink(1)
                    window.loaded(({'name': 'Example'}, [
                        {'file_id': 10, 'name': 'Next-gen', 'version': '1', 'category_id': 1},
                        {'file_id': 11, 'name': 'Optional patch', 'version': '1', 'category_id': 3},
                        {'file_id': 12, 'name': 'Old', 'version': '0', 'category_id': 4}]))
                    self.assertEqual(window.table.rowCount(), 2)
                    window.old.setChecked(True)
                    self.assertEqual(window.table.rowCount(), 3)
                finally:
                    window.sync_timer.stop()
                    window.close()

    def test_required_fomod_handoff_installs_into_temporary_game(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            game, documents, config = folder / 'game', folder / 'documents', folder / 'manager'
            exe = game / 'bin/x64_dx12/witcher3.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()  # A path marker, never executed.
            (game / 'content').mkdir()
            documents.mkdir()
            config.mkdir()
            (config / 'installed.xml').write_text('<installed/>')
            (config / 'config.ini').write_text(
                f'[PATHS]\ngameexe={exe.as_posix()}\ndocuments={documents.as_posix()}\n'
                '[SETTINGS]\nallowpopups=0\ntheme=Dark\nlanguage=English.qm\nusenativedialog=0\n[TOOLBAR]\n')
            archive = folder / 'BridgeTest.zip'
            payload = 'Mods/modBridgeTest/content/scripts/local/bridgeTest.ws'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr(payload, '// test fixture\n')
                z.writestr('fomod/ModuleConfig.xml', '<config><moduleName>Test</moduleName><requiredInstallFiles><folder source="Mods" destination="Mods"/></requiredInstallFiles></config>')
            _, components = inspect_install_archive(archive, bridge.ROOT / 'tools/7zip/7z.exe')
            self.assertEqual(components, ['Mods/modBridgeTest'])
            with patch.object(bridge, 'USER_DIR', folder / 'private'), patch.object(bridge, 'running_game', return_value=False):
                manager = bridge.Manager(self.app, config)
                try:
                    with patch.object(manager, 'show'):
                        result = manager.install([str(archive)])
                    self.assertEqual(result, (1, 0, 0))
                    self.assertEqual((game / payload).read_text(), '// test fixture\n')
                    self.assertEqual(read_inventory(config)[0].components, (('data', 'modBridgeTest'),))
                finally:
                    manager.save()
                    manager.widget.modsSettingsWatcher.stop()
                    self.app.aboutToQuit.disconnect(manager.save)
                    self.app.aboutToQuit.disconnect(manager.widget.modsSettingsWatcher.stop)
                    manager.model.lock.release()
                    manager.window.close()


if __name__ == '__main__':
    unittest.main()

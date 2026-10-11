"""Qt integration checks against a disposable W3MM inventory, never the user's game."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
import bridge


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


if __name__ == '__main__':
    unittest.main()

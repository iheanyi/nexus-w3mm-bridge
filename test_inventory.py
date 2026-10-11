import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_core import BridgeError
from inventory import (InstalledMod, archive_layout, binding_for, library_path, read_inventory, validate_required_fomod,
                       read_library, save_library, update_status)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.entry = InstalledMod('Main', True, 'date1', (('data', 'modMain'),))
        self.file = dict(file_id=10, name='Main next-gen', version='1', category_id=1, uploaded_timestamp=100)
        self.binding = binding_for(self.entry, 5, self.file)

    def test_refresh_reads_add_remove_enable_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            xml = path / 'installed.xml'
            xml.write_text('<installed><mod name="A" enabled="True" date="x"><data>modA</data><dlc>dlcA</dlc></mod></installed>')
            before = read_inventory(path)
            xml.write_text('<installed><mod name="B" enabled="False" date="y"><menu>b.xml</menu></mod></installed>')
            after = read_inventory(path)
            self.assertEqual(before[0].components, (('data', 'modA'), ('dlc', 'dlcA')))
            self.assertEqual([m.name for m in after], ['B'])
            self.assertFalse(after[0].enabled)
            self.assertEqual(xml.read_text(), '<installed><mod name="B" enabled="False" date="y"><menu>b.xml</menu></mod></installed>')

    def test_legacy_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'installed.xml').write_bytes(b'<?xml version="1.0" encoding="utf_8"?><installed/>')
            self.assertEqual(read_inventory(path), [])

    def test_incomplete_inventory_does_not_become_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'installed.xml').write_text('<installed><mod')
            with self.assertRaises(BridgeError):
                read_inventory(path)

    def test_settings_are_scoped_to_manager(self):
        self.assertNotEqual(library_path(Path('root'), Path('managerA')), library_path(Path('root'), Path('managerB')))

    def test_installed_version_invalidated_by_external_reinstall(self):
        entry = InstalledMod('Main', True, 'date2', self.entry.components)
        status, target = update_status(entry, self.binding, [self.file])
        self.assertIn('Changed in W3MM', status)
        self.assertIsNone(target)

    def test_enable_toggle_keeps_binding(self):
        entry = InstalledMod('Main', False, 'date1', self.entry.components)
        self.assertEqual(entry.key, self.entry.key)
        self.assertEqual(update_status(entry, self.binding, [self.file]), ('No newer files', None))

    def test_optional_does_not_replace_main(self):
        optional = dict(self.file, file_id=11, name='Optional patch', category_id=3, uploaded_timestamp=200)
        status, target = update_status(self.entry, self.binding, [self.file, optional])
        self.assertEqual(status, 'New files — choose variant')
        self.assertIsNone(target)

    def test_different_game_variant_does_not_replace_selected(self):
        classic = dict(self.file, file_id=11, name='Main classic', uploaded_timestamp=200)
        self.assertIsNone(update_status(self.entry, self.binding, [self.file, classic])[1])

    def test_same_name_new_version_is_only_review_suggestion(self):
        updated = dict(self.file, file_id=11, version='2', uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [self.file, updated]), ('Newer file: 2 — review', 11))

    def test_multiple_candidates_require_choice(self):
        a = dict(self.file, file_id=11, version='2', uploaded_timestamp=200)
        b = dict(self.file, file_id=12, version='3', uploaded_timestamp=300)
        self.assertIsNone(update_status(self.entry, self.binding, [self.file, a, b])[1])

    def test_missing_and_unknown_file_are_not_up_to_date(self):
        self.assertIn('unavailable', update_status(self.entry, self.binding, [])[0])
        self.assertEqual(update_status(self.entry, dict(self.binding, file_id=None), [self.file])[0], 'Choose installed file')

    def test_no_account_keys_persisted(self):
        binding = binding_for(self.entry, 5, dict(self.file, key='secret', expires=100, URI='signed'))
        self.assertNotIn('secret', str(binding))
        self.assertNotIn('signed', str(binding))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'library.json'
            save_library(path, {self.entry.key: binding})
            self.assertEqual(read_library(path)[self.entry.key]['file_id'], 10)

    def test_multiple_components_one_archive(self):
        self.assertEqual(archive_layout(['Mods/modA/content/a.ws', 'DLC/dlcB/content/b.bundle']), ['DLC/dlcB', 'Mods/modA'])

    def test_internal_alternatives_are_blocked(self):
        for members in (['Option A/modX/content/x.ws', 'Option B/modX/content/x.ws'], ['fomod/ModuleConfig.xml']):
            with self.assertRaises(BridgeError):
                archive_layout(members)

    def test_archive_traversal_is_rejected(self):
        for name in ('../modA/content/x.ws', '/modA/content/a', 'C:/modA/content/a'):
            with self.assertRaises(BridgeError):
                archive_layout([name])

    def test_required_fomod_with_standard_folders_is_supported(self):
        xml = b'<config><moduleName>Example</moduleName><requiredInstallFiles><folder source="Mods" destination="Mods"/><folder source="bin" destination="bin"/></requiredInstallFiles></config>'
        validate_required_fomod(xml, ['Mods/modA/content/a.ws', 'bin/config/base/a.ini', 'fomod/ModuleConfig.xml'])

    def test_fomod_choices_remapping_and_unselected_payload_are_rejected(self):
        templates = [
            '<config><requiredInstallFiles><folder source="Mods" destination="Mods"/></requiredInstallFiles><installSteps/></config>',
            '<config><requiredInstallFiles><folder source="OptionA" destination="Mods"/></requiredInstallFiles></config>',
            '<config><requiredInstallFiles><folder source="Mods" destination="../Mods"/></requiredInstallFiles></config>',
            '<config><requiredInstallFiles><folder source="bin" destination="bin"/></requiredInstallFiles></config>',
        ]
        for xml in templates:
            with self.subTest(xml=xml), self.assertRaises(BridgeError):
                validate_required_fomod(xml.encode(), ['Mods/modA/content/a.ws', 'fomod/ModuleConfig.xml'])


if __name__ == '__main__':
    unittest.main()

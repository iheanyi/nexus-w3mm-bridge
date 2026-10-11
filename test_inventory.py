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
        self.assertEqual(update_status(entry, self.binding, [self.file]), ('Up to date', None))

    def test_optional_does_not_replace_main(self):
        optional = dict(self.file, file_id=11, name='Optional patch', category_id=3, uploaded_timestamp=200)
        status, target = update_status(self.entry, self.binding, [self.file, optional])
        self.assertEqual(status, 'Up to date')
        self.assertIsNone(target)

    def test_different_game_variant_does_not_replace_selected(self):
        classic = dict(self.file, file_id=11, name='Main classic', uploaded_timestamp=200)
        self.assertIsNone(update_status(self.entry, self.binding, [self.file, classic])[1])

    def test_same_name_new_version_is_only_review_suggestion(self):
        updated = dict(self.file, file_id=11, version='2', uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [self.file, updated]), ('Newer version: 2 — review', 11))

    def test_versioned_filename_matches_only_its_variant(self):
        installed = dict(self.file, name='SmartAutoLoot Remaster V1.1.3', version='1.1.3')
        variant = dict(installed, file_id=11, name='SmartAutoLoot NextGen V1.1.4 Patch4.04', version='1.1.4', uploaded_timestamp=300)
        newer = dict(installed, file_id=12, name='SmartAutoLoot Remaster V1.1.4', version='1.1.4', uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [installed, variant]), ('Up to date', None))
        self.assertEqual(update_status(self.entry, self.binding, [installed, variant, newer]), ('Newer version: 1.1.4 — review', 12))

    def test_loot_alternatives_and_menu_only_files_do_not_trigger_update(self):
        cases = [
            ('SmartAutoLoot Remaster V1.1.3', '1.1.3', [
                ('SmartAutoLoot NextGen V1.1.3 Patch4.04', '1.1.3', 3),
                ('SmartAutoLoot Menu Config XML Only', '1.1.3', 3)]),
            ('Increased Creature Loot Ultimate Remastered V5.0.1', '5.0.1', [
                ('Increased Creature Loot Max Remastered V5.0.1', '5.0.1', 1),
                ('ICL Ultimate Remastered - Max Compat V5.0.1', '5.0.1', 3),
                ('ModICL Cheat Remastered V5.0.0', '5.0.0', 5)]),
        ]
        for name, version, alternatives in cases:
            current = dict(self.file, name=name, version=version)
            files = [current] + [dict(self.file, file_id=11+i, name=n, version=v, category_id=c, uploaded_timestamp=200+i)
                                 for i, (n, v, c) in enumerate(alternatives)]
            with self.subTest(name=name):
                self.assertEqual(update_status(self.entry, self.binding, files), ('Up to date', None))

    def test_same_version_reupload_is_distinguished_from_new_version(self):
        reupload = dict(self.file, file_id=11, uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [self.file, reupload]), ('Re-uploaded file — review', 11))
        digest_url = 'https://www.virustotal.com/gui/file/' + 'a' * 64
        current = dict(self.file, external_virus_scan_url=digest_url)
        identical = dict(reupload, external_virus_scan_url=digest_url)
        self.assertEqual(update_status(self.entry, self.binding, [current, identical]), ('Up to date', None))

    def test_new_upload_of_older_version_is_not_an_update(self):
        current = dict(self.file, name='Main v1.10', version='1.10')
        older = dict(current, file_id=11, name='Main v1.9', version='1.9', uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [current, older]), ('Up to date', None))

    def test_game_patch_number_is_not_removed_from_variant_name(self):
        current = dict(self.file, name='Main v1.0 Patch4.04', version='1.0')
        alternative = dict(current, file_id=11, name='Main v1.1 Patch5.01', version='1.1', uploaded_timestamp=200)
        self.assertEqual(update_status(self.entry, self.binding, [current, alternative]), ('Up to date', None))

    def test_archived_version_still_finds_newer_matching_variant(self):
        current = dict(self.file, name='Main v1.0', version='1.0', category_id=7)
        newer = dict(self.file, file_id=11, name='Main v1.1', version='1.1', uploaded_timestamp=200)
        optional = dict(newer, file_id=12, category_id=3, uploaded_timestamp=300)
        self.assertEqual(update_status(self.entry, self.binding, [current, newer, optional]), ('Newer version: 1.1 — review', 11))

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

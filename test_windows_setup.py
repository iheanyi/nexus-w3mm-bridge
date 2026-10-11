import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge
from bridge_core import BridgeError
from windows_setup import manager_config_candidates, user_folder, vortex_arguments


class WindowsSetupTests(unittest.TestCase):
    def test_redirected_folders_and_missing_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            redirected = Path(temp) / 'Profil é/Downloads'
            with patch('windows_setup.shell.SHGetKnownFolderPath', return_value=str(redirected)) as known:
                self.assertEqual(user_folder('downloads'), redirected)
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(user_folder('local'), redirected)
                    self.assertEqual(user_folder('roaming'), redirected)
                self.assertEqual(known.call_count, 3)

    def test_explicit_profile_environment_is_respected(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': r'Z:\Profile é\Local', 'APPDATA': r'Z:\Profile é\Roaming'}):
            self.assertEqual(user_folder('local'), Path(os.environ['LOCALAPPDATA']))
            self.assertEqual(user_folder('roaming'), Path(os.environ['APPDATA']))

    def test_manager_beside_exe_or_in_redirected_documents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            exe = root / 'App folder/TheWitcher3ModManager.exe'
            exe.parent.mkdir()
            exe.touch()
            config = root / 'Documents é/The Witcher 3 Mod Manager'
            config.mkdir(parents=True)
            for name in ('config.ini', 'installed.xml'):
                (config / name).touch()
            folders = {'documents': config.parent, 'local': root / 'Local', 'roaming': root / 'Roaming'}
            with patch('windows_setup.user_folder', side_effect=folders.__getitem__):
                self.assertEqual(manager_config_candidates(exe), [config])
                self.assertEqual(manager_config_candidates(config / 'installed.xml'), [config])
                # Missing explicitly selected inventory must not pick another one.
                self.assertEqual(manager_config_candidates(exe.parent / 'installed.xml'), [])
                for name in ('config.ini', 'installed.xml'):
                    (exe.parent / name).touch()
                self.assertEqual(manager_config_candidates(exe), [exe.parent])

    def test_multiple_manager_inventories_are_returned_for_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            folders = {kind: root / kind for kind in ('documents', 'local', 'roaming')}
            expected = []
            for kind in ('documents', 'local'):
                config = folders[kind] / 'The Witcher 3 Mod Manager'
                config.mkdir(parents=True)
                (config / 'config.ini').touch()
                (config / 'installed.xml').touch()
                expected.append(config)
            with patch('windows_setup.user_folder', side_effect=folders.__getitem__):
                self.assertEqual(manager_config_candidates(root / 'W3MM.exe'), expected)

    def test_custom_vortex_location_keeps_flags_and_url_as_one_argument(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / 'Applications é/VORTEX.exe'
            exe.parent.mkdir()
            exe.touch()
            command = subprocess.list2cmdline([str(exe), '--custom-flag', '-d', '%1'])
            url = 'nxm://skyrim/mods/1/files/2?key=hello&expires=10'
            self.assertEqual(vortex_arguments(command, url), [str(exe), '--custom-flag', '-d', url])
            with patch.object(bridge, 'registered_nxm_command', return_value=bridge.protocol_command()), \
                    patch.object(bridge, 'read_prefs', return_value={'previous_nxm_command': command}), \
                    patch.object(bridge.subprocess, 'Popen') as launch:
                self.assertTrue(bridge.forward_other_game(url))
                launch.assert_called_once_with([str(exe), '--custom-flag', '-d', url], shell=False)
            with self.assertRaises(ValueError):
                vortex_arguments(subprocess.list2cmdline([str(exe), '-d']), url)

    def test_invalid_or_missing_handler_gives_actionable_error(self):
        with patch.object(bridge, 'registered_nxm_command', return_value=''), \
                patch.object(bridge.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(BridgeError, 'Re-register'):
                bridge.forward_other_game('nxm://skyrim/mods/1/files/2')
            launch.assert_not_called()

    def test_moving_bridge_keeps_original_vortex_handler(self):
        with tempfile.TemporaryDirectory() as temp:
            import winreg
            exe = Path(temp) / 'Vortex.exe'
            exe.touch()
            original = subprocess.list2cmdline([str(exe), '-d', '%1'])
            prefs = {'previous_nxm_command': original}
            with patch.object(bridge, 'registered_nxm_command', return_value='"Z:\\Old Folder\\NexusW3MMBridge.exe" "%1"'), \
                    patch.object(bridge, 'save_prefs') as save, patch.object(winreg, 'CreateKey'), \
                    patch.object(winreg, 'SetValueEx') as write:
                bridge.set_protocol(True, prefs)
                self.assertEqual(prefs['previous_nxm_command'], original)
                save.assert_called_once_with(prefs)
                self.assertEqual(write.call_args.args[-1], bridge.protocol_command())


if __name__ == '__main__':
    unittest.main()

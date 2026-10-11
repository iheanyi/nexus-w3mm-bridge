import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_core import BridgeError, ModLink, NexusBridge, parse_link, safe_archive_name


class Response:
    headers = {}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, *_):
        data, self.data = self.data, b''
        return data


class Tests(unittest.TestCase):
    def test_mod_links(self):
        self.assertEqual(parse_link('https://www.nexusmods.com/witcher3/mods/13130?tab=files'), ModLink(13130))
        self.assertEqual(parse_link('13130'), ModLink(13130))
        self.assertEqual(parse_link('https://nexusmods.com/witcher3/mods/13130?file_id=78022').file_id, 78022)

    def test_free_download_access_parameters(self):
        self.assertEqual(parse_link('nxm://witcher3/mods/13130/files/78022?key=example&expires=123'), ModLink(13130, 78022, 'example', 123))

    def test_rejects_wrong_game_or_host(self):
        for url in ('https://evil.example/witcher3/mods/12', 'https://nexusmods.com.evil.example/witcher3/mods/12', 'nxm://skyrim/mods/12/files/30', 'https://nexusmods.com@evil.example/witcher3/mods/12', 'http://nexusmods.com/witcher3/mods/12'):
            with self.subTest(url=url), self.assertRaises(BridgeError):
                parse_link(url)

    def test_filenames(self):
        self.assertEqual(safe_archive_name('Friendly HUD 1.0.7z'), 'Friendly HUD 1.0.7z')
        for name in ('../test.zip', '..\\test.zip', 'C:\\test.zip', 'payload.exe', 'file.zip:payload', 'CON.zip', 'file.zip\n'):
            with self.subTest(name=name), self.assertRaises(BridgeError):
                safe_archive_name(name)

    def test_rpc_only_allows_download_operations(self):
        with self.assertRaises(BridgeError):
            NexusBridge(Path('missing')).rpc('vortex_remove_mod')

    def test_missing_bridge(self):
        with self.assertRaisesRegex(BridgeError, 'Open Vortex'):
            NexusBridge(Path('missing')).rpc('nexus_account')

    def make_download(self, folder, data=b'archive', size=None, digest=None, cancel=None):
        bridge = NexusBridge()
        file = {'file_id': 2, 'file_name': 'test.zip', 'size_in_bytes': len(data) if size is None else size,
                'external_virus_scan_url': 'https://www.virustotal.com/gui/file/' + (digest or hashlib.sha256(data).hexdigest())}
        with patch.object(bridge, 'rpc', return_value={'data': [{'URI': 'https://cdn.example/file?secret=DO_NOT_LOG'}]}) as rpc:
            with patch('urllib.request.OpenerDirector.open', return_value=Response(data)):
                result = bridge.download(ModLink(1, 2, 'access', 123), file, Path(folder), lambda *args: None, cancel or threading.Event())
        return result, rpc

    def test_download_verifies_before_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            result, rpc = self.make_download(folder)
            self.assertEqual(result.read_bytes(), b'archive')
            self.assertEqual(rpc.call_args.kwargs['key'], 'access')
            self.assertFalse(result.with_name('test.zip.part').exists())

    def test_hash_failure_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(BridgeError, 'verification'):
                self.make_download(folder, digest='0' * 64)
            self.assertEqual(list(Path(folder).rglob('*.zip')), [])
            self.assertEqual(list(Path(folder).rglob('*.part')), [])

    def test_truncated_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(BridgeError, 'incomplete'):
                self.make_download(folder, size=200)

    def test_cancel_does_not_handoff(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(BridgeError, 'canceled'):
                self.make_download(folder, cancel=cancel)
            self.assertEqual(list(Path(folder).rglob('*.part')), [])

    def test_cached_archive_is_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            result, _ = self.make_download(folder)
            second, rpc = self.make_download(folder)
            self.assertEqual(result, second)
            rpc.assert_not_called()


if __name__ == '__main__':
    unittest.main()

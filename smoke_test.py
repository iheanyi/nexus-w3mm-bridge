"""Offline packaged-runtime check; all writable state lives in a temporary profile."""
import json
import tempfile
import traceback
import zipfile
from pathlib import Path


def run(bridge, app, report):
    checks = []
    window = manager = None
    result = {'ok': False, 'checks': checks}
    try:
        with tempfile.TemporaryDirectory(prefix='W3MM smoke ') as temporary:
            root = Path(temporary)
            bridge.USER_DIR = root / 'profile'
            bridge.PREFS_PATH = bridge.USER_DIR / 'settings.json'
            bridge.DOWNLOADS = bridge.USER_DIR / 'downloads'
            try:
                assert bridge.read_prefs() == {}, 'Release contains nonempty defaults'
                for relative in ('res/w3a.ico', 'translations/English.qm', 'tools/7zip/7z.exe', 'tools/7zip/7z.dll'):
                    assert (bridge.ROOT / relative).is_file(), f'Missing asset: {relative}'
                window = bridge.BridgeWindow(connect_on_start=False)
                assert window.installed_table.rowCount() == 0
                assert not window.workers
                assert not window.grab().isNull()
                app.processEvents()
                checks.append('fresh offline Qt window and bundled assets')

                game, docs, config = root / 'Game é', root / 'Documents', root / 'Manager é'
                exe = game / 'bin/x64/witcher3.exe'
                exe.parent.mkdir(parents=True)
                exe.touch()  # A path marker; never executed.
                (game / 'content').mkdir()
                docs.mkdir()
                config.mkdir()
                (config / 'installed.xml').write_text('<installed/>', encoding='utf-8')
                (config / 'config.ini').write_text(
                    f'[PATHS]\ngameexe={exe.as_posix()}\ndocuments={docs.as_posix()}\n'
                    '[SETTINGS]\nallowpopups=0\ntheme=Dark\nlanguage=English.qm\nusenativedialog=0\n[TOOLBAR]\n', encoding='utf-8')
                bridge.save_prefs({'manager_config_dir': str(config)})
                assert bridge.read_prefs()['manager_config_dir'] == str(config)
                manager = bridge.Manager(app, config)
                assert not manager.model.all()
                checks.append('embedded W3MM and per-user settings')

                bridge.DOWNLOADS.mkdir(parents=True)
                archive = bridge.DOWNLOADS / 'Archive é.zip'
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('Mods/modSmoke/content/test.ws', '// smoke fixture')
                _, components = bridge.inspect_install_archive(archive, bridge.ROOT / 'tools/7zip/7z.exe')
                assert components == ['Mods/modSmoke']
                from src.core.fetcher import extractArchive
                extractArchive(str(archive), str(root / 'Extract é'))
                assert (root / 'Extract é/Mods/modSmoke/content/test.ws').read_text() == '// smoke fixture'
                checks.append('bundled 7-Zip listing and extraction with Unicode paths')
                result['ok'] = True
            finally:
                if manager:
                    manager.save()
                    manager.widget.modsSettingsWatcher.stop()
                    app.aboutToQuit.disconnect(manager.save)
                    app.aboutToQuit.disconnect(manager.widget.modsSettingsWatcher.stop)
                    manager.model.lock.release()
                    manager.window.close()
                if window:
                    window.sync_timer.stop()
                    window.close()
    except Exception:
        result['ok'] = False
        result['error'] = traceback.format_exc()
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1

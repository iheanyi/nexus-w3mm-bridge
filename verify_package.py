"""Exercise the shipped EXE after relocation, without the developer's environment."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def verify(package):
    package = Path(package).resolve()
    private_names = {'settings.json', 'config.ini', 'installed.xml', 'nexus-cache.json', 'bridge.json'}
    for file in package.rglob('*'):
        if file.is_file() and (file.name in private_names or file.name.startswith('library-')
                              or file.suffix.lower() in ('.part', '.log')):
            raise RuntimeError(f'Private state in release: {file.relative_to(package)}')
    assert json.loads((package / 'defaults.json').read_text('utf-8-sig')) == {}
    with tempfile.TemporaryDirectory(prefix='W3MM package ') as temporary:
        root = Path(temporary)
        relocated = root / 'Apps é with spaces' / 'NexusW3MMBridge'
        shutil.copytree(package, relocated)
        cwd = root / 'Unrelated working directory'
        cwd.mkdir()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(('PYTHON', 'QT_', 'PYSIDE')) or key in ('VIRTUAL_ENV', 'CONDA_PREFIX'):
                env.pop(key)
        for key, relative in [('LOCALAPPDATA', 'Profile/Local'), ('APPDATA', 'Profile/Roaming')]:
            directory = root / relative
            directory.mkdir(parents=True)
            env[key] = str(directory)
        system_root = os.environ['SystemRoot']  # os.environ is case-insensitive on Windows.
        env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root])
        report = root / 'smoke.json'
        result = subprocess.run([str(relocated / 'NexusW3MMBridge.exe'), '--smoke-test', str(report)],
                                cwd=cwd, env=env, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
        if not report.is_file():
            raise RuntimeError(f'Packaged app did not start or write its report (exit {result.returncode})')
        data = json.loads(report.read_text('utf-8'))
        if result.returncode or not data.get('ok'):
            raise RuntimeError(data.get('error', f'Packaged app failed: {result.returncode}'))
        print('Relocated package passed with a fresh profile and a Windows-only PATH:')
        for check in data['checks']:
            print('  ' + check)


if __name__ == '__main__':
    verify(sys.argv[1])

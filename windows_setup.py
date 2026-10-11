"""Per-user Windows locations and existing application registrations."""
import os
from pathlib import Path

import win32api
from win32com.shell import shell


def user_folder(name):
    environment = {'local': 'LOCALAPPDATA', 'roaming': 'APPDATA'}
    if name in environment and os.environ.get(environment[name]):
        return Path(os.environ[environment[name]])
    identifiers = {'local': shell.FOLDERID_LocalAppData, 'roaming': shell.FOLDERID_RoamingAppData,
                   'documents': shell.FOLDERID_Documents, 'downloads': shell.FOLDERID_Downloads}
    # Known folders honor OneDrive and redirected/localized Windows profiles.
    return Path(shell.SHGetKnownFolderPath(identifiers[name], 0, None))


def manager_config_candidates(selected):
    selected = Path(selected).resolve()
    folder = selected if selected.is_dir() else selected.parent
    def configured(path):
        return (path / 'config.ini').is_file() and (path / 'installed.xml').is_file()
    if configured(folder):
        return [folder]
    # Explicit inventory selections must never silently switch to another one.
    if selected.suffix.lower() != '.exe':
        return []
    candidates = [user_folder(kind) / 'The Witcher 3 Mod Manager' for kind in ('documents', 'local', 'roaming')]
    return list(dict.fromkeys(path.resolve() for path in candidates if configured(path)))


def registered_nxm_command():
    import winreg
    # HKCR includes per-machine associations when no per-user override exists.
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'nxm\shell\open\command') as key:
            return winreg.QueryValueEx(key, '')[0]
    except OSError:
        return ''


def command_arguments(command):
    if not isinstance(command, str) or not command.strip() or any(c in command for c in '\r\n\x00'):
        return []
    return win32api.CommandLineToArgv(os.path.expandvars(command))


def vortex_arguments(command, url):
    """Preserve the registered Vortex location/flags; never invoke a shell."""
    args = command_arguments(command)
    if (not args or Path(args[0]).name.casefold() != 'vortex.exe'
            or not Path(args[0]).is_absolute() or not Path(args[0]).is_file()):
        raise ValueError('The registered Vortex executable could not be found.')
    placeholders = ('%1', '%l', '%L')
    if not any(token in arg for arg in args[1:] for token in placeholders):
        raise ValueError('The Vortex registration has no download link argument.')
    for i in range(1, len(args)):
        for token in placeholders:
            args[i] = args[i].replace(token, url)
    return args


def is_bridge_command(command):
    args = command_arguments(command)
    return bool(args and (Path(args[0]).name.casefold() == 'nexusw3mmbridge.exe'
                         or (Path(args[0]).name.casefold() in ('python.exe', 'pythonw.exe')
                             and len(args) > 1 and Path(args[1]).name.casefold() == 'bridge.py')))

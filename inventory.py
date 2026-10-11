"""Read W3MM inventory without taking its write lock; track exact Nexus files.

W3MM owns installation state. This module never writes installed.xml.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bridge_core import BridgeError, expected_digest, file_digest


@dataclass(frozen=True)
class InstalledMod:
    name: str
    enabled: bool
    date: str
    components: tuple[tuple[str, str], ...]

    @property
    def key(self):
        value = json.dumps([self.name, sorted(self.components)], ensure_ascii=False)
        return hashlib.sha256(value.encode()).hexdigest()[:24]


def read_inventory(folder: Path) -> list[InstalledMod]:
    filename = folder / 'installed.xml'
    try:
        raw = filename.read_bytes()
        raw = raw.replace(b'encoding="utf_8"', b'encoding="utf-8"').replace(b"encoding='utf_8'", b"encoding='utf-8'")
        root = ET.fromstring(raw)
        if root.tag != 'installed':
            raise ValueError('Invalid root')
        result = []
        for node in root.findall('mod'):
            if not node.get('name'):
                raise ValueError('Missing name')
            components = tuple((tag, item.text or '') for tag in ('data', 'dlc', 'menu', 'ini') for item in node.findall(tag))
            result.append(InstalledMod(node.get('name'), node.get('enabled') == 'True', node.get('date', ''), components))
        return result
    except FileNotFoundError:
        raise BridgeError('W3MM inventory not found. Choose your existing W3MM installation in Settings.') from None
    except (OSError, ValueError, ET.ParseError, LookupError):
        raise BridgeError('W3MM is saving its inventory, or the file could not be read. Your previous list is kept; retry Refresh.') from None


def from_model(model):
    return [InstalledMod(m.name, m.enabled, m.date, tuple((tag, name) for tag, names in
            (('data', m.files), ('dlc', m.dlcs), ('menu', m.menus), ('ini', m.inis)) for name in names)) for m in model.all()]


def missing_payloads(entry, game):
    """A saved inventory row cannot prove that its mod folders still exist."""
    if not game:
        return []
    missing = []
    for tag, name in entry.components:
        if tag not in ('data', 'dlc'):
            continue
        root = game / ('Mods' if tag == 'data' else 'DLC')
        folder = root / name
        if not folder.is_dir() and not (tag == 'data' and not entry.enabled and (root / ('~' + name)).is_dir()):
            missing.append(name)
    return missing


def library_path(root: Path, config_dir: Path):
    scope = hashlib.sha256(str(config_dir.resolve()).casefold().encode()).hexdigest()[:16]
    return root / f'library-{scope}.json'


def read_library(path):
    try:
        value = json.loads(path.read_text('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_library(path, bindings):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(bindings, indent=2), encoding='utf-8')
    temporary.replace(path)


def binding_for(entry, mod_id, file, *, evidence='Selected by user'):
    # Only public metadata is persisted, never nxm keys or signed download URLs.
    return dict(mod_id=int(mod_id), file_id=int(file['file_id']), name=file.get('name', ''),
                version=file.get('version', ''), category_id=file.get('category_id'),
                uploaded_timestamp=int(file.get('uploaded_timestamp') or 0),
                inventory_date=entry.date, evidence=evidence)


def replaced_entries(previous, installed, bindings, mod_id, file, reviewed_key=None):
    """Retire only fully superseded main packages on the same Nexus page.

    Optional files, disabled alternatives and records owning extra components
    need separate review. A shared page or similar display name is not enough.
    """
    if file.get('category_id') not in (1, 2):
        return []
    components = {(tag, name.casefold()) for tag, name in installed.components}
    roots = {c for c in components if c[0] in ('data', 'dlc')}
    if not roots or not installed.enabled:
        return []
    result = []
    for old in previous:
        binding = bindings.get(old.key, {})
        old_components = {(tag, name.casefold()) for tag, name in old.components}
        old_roots = {c for c in old_components if c[0] in ('data', 'dlc')}
        category = binding.get('category_id')
        known_main = category in (1, 2)
        reviewed_archive = old.key == reviewed_key and category in (4, 6, 7)
        if (old.name != installed.name and old.enabled and binding.get('mod_id') == mod_id
                and binding.get('inventory_date') == old.date
                and (known_main or reviewed_archive) and old_roots == roots
                and old_components <= components):
            result.append(old)
    return result


def update_status(entry, binding, files):
    """Return a review suggestion, never an automatic variant substitution."""
    if not binding:
        return 'Link to Nexus', None
    if binding.get('inventory_date') != entry.date:
        return 'Changed in W3MM — relink', None
    if not binding.get('file_id'):
        return 'Choose installed file', None
    if files is None:
        return 'Not checked', None
    current = next((f for f in files if f.get('file_id') == binding.get('file_id')), None)
    if not current:
        return 'Installed file unavailable — review', None
    stamp = int(current.get('uploaded_timestamp') or 0)
    active = [f for f in files if f.get('category_id') in (1, 2, 3, 5)]
    newer = [f for f in active if int(f.get('uploaded_timestamp') or 0) > stamp]
    # An exact file name is a conservative hint, not a guarantee of compatibility.
    same = [f for f in newer if f.get('name', '').strip().casefold() == current.get('name', '').strip().casefold()
            and (current.get('category_id') == 4 or f.get('category_id') == current.get('category_id'))]
    if len(same) == 1:
        return f"Newer file: {same[0].get('version', '?')} — review", same[0]['file_id']
    if newer:
        return 'New files — choose variant', None
    if current.get('category_id') in (4, 6, 7):
        return 'Old / archived file — review', None
    return 'No newer files', None


def game_directory(config_dir):
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_dir / 'config.ini', encoding='utf-8-sig')
    exe = config.get('PATHS', 'gameexe', fallback='')
    return Path(exe).parent.parent.parent if exe else None


def archive_members(archive, seven_zip):
    result = subprocess.run([str(seven_zip), 'l', '-slt', '-ba', '-sccUTF-8', str(archive)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=45)
    if result.returncode:
        raise BridgeError('Could not inspect this archive.')
    return [line[7:].replace('\\', '/') for line in result.stdout.splitlines() if line.startswith('Path = ')]


def archive_layout(members, *, inspection_only=False):
    folders = set()
    for member in members:
        parts = PurePosixPath(member).parts
        if member.startswith('/') or '..' in parts or any(':' in p for p in parts):
            raise BridgeError('Archive contains an unsafe path.')
        for i, part in enumerate(parts[:-1]):
            if parts[i + 1].casefold() == 'content' and part.casefold().startswith(('mod', 'dlc')):
                folders.add('/'.join(parts[:i + 1]))
    names = [PurePosixPath(p).name.casefold() for p in folders]
    if not inspection_only and (len(names) != len(set(names)) or any(p.casefold().endswith('fomod/moduleconfig.xml') for p in members)):
        raise BridgeError('This archive contains an installer or alternative folders with the same name. Follow its manual installation instructions; the bridge will not install every alternative.')
    return sorted(folders)


def validate_required_fomod(xml, members):
    """Accept only a plain copy of canonical game roots, with no installer choices."""
    message = ('This archive needs installer choices or custom file placement. '
               'Follow the author’s manual instructions; the bridge will not install every alternative.')
    try:
        if len(xml) > 1024 * 1024 or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ValueError('Unsupported XML')
        root = ET.fromstring(xml)
        if root.tag != 'config' or any(child.tag not in {'moduleName', 'moduleImage', 'requiredInstallFiles'} for child in root):
            raise ValueError('Custom installer')
        required = root.findall('requiredInstallFiles')
        if len(required) != 1 or not len(required[0]):
            raise ValueError('Missing files')
        roots = set()
        for item in required[0]:
            source = item.get('source', '').replace('\\', '/').rstrip('/').casefold()
            destination = item.get('destination', '').replace('\\', '/').rstrip('/').casefold()
            if (item.tag != 'folder' or len(item) or source not in {'mods', 'dlc', 'bin'} or source != destination
                    or set(item.attrib) - {'source', 'destination', 'priority'}):
                raise ValueError('Noncanonical copy')
            roots.add(source)
        for member in members:
            parts = PurePosixPath(member).parts
            first = parts[0].casefold() if parts else ''
            if first in {'mods', 'dlc', 'bin'} and first not in roots:
                raise ValueError('Unselected game files')
        folders = archive_layout([m for m in members if not m.casefold().endswith('fomod/moduleconfig.xml')])
        if any(PurePosixPath(folder).parts[0].casefold() not in roots for folder in folders):
            raise ValueError('Unselected mod folder')
    except (ET.ParseError, ValueError, TypeError):
        raise BridgeError(message) from None


def inspect_install_archive(archive, seven_zip):
    members = archive_members(archive, seven_zip)
    archive_layout(members, inspection_only=True)  # Always validate paths first.
    configs = [m for m in members if m.casefold().endswith('fomod/moduleconfig.xml')]
    if configs:
        if len(configs) != 1 or configs[0].casefold() != 'fomod/moduleconfig.xml':
            raise BridgeError('This archive has a custom installer layout. Follow its manual installation instructions.')
        result = subprocess.run([str(seven_zip), 'x', '-so', '-spd', str(archive), configs[0]],
                                capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=45)
        if result.returncode:
            raise BridgeError('Could not read the archive’s installer instructions.')
        validate_required_fomod(result.stdout, members)
    folders = archive_layout([m for m in members if m not in configs])
    return members, folders


def guess_mod_id(name):
    match = re.search(r' (\d+) \S+ \d{4}-\d{2}-\d{2}T', name)
    if not match:
        match = re.search(r'-(\d+)-.+-\d{9,}', name)
    return int(match[1]) if match else None


def archive_matches(entry, members, archive, game, seven_zip):
    """Compare installed payload bytes; archive presence alone proves nothing."""
    roots = {name.casefold(): tag for tag, name in entry.components if tag in ('data', 'dlc')}
    if not roots or not game:
        return False
    matched = set()
    count = 0
    for member in members:
        parts = PurePosixPath(member).parts
        for i, part in enumerate(parts[:-1]):
            if part.casefold() in roots and parts[i + 1].casefold() == 'content' and len(parts) > i + 2:
                tag = roots[part.casefold()]
                folder = game / ('Mods' if tag == 'data' else 'DLC') / part
                if not folder.exists() and tag == 'data':
                    folder = folder.with_name('~' + part)
                target = folder.joinpath(*parts[i + 1:])
                if not target.is_file() and not entry.enabled and tag == 'dlc':
                    target = target.with_name(target.name + '.disabled')
                if not target.is_file():
                    # 7z listing includes directories as well as files.
                    if target.is_dir():
                        continue
                    return False
                result = subprocess.run([str(seven_zip), 'x', '-so', '-spd', str(archive), member],
                                        capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=45)
                if result.returncode or hashlib.sha256(result.stdout).hexdigest() != file_digest(target):
                    return False
                matched.add(part.casefold())
                count += 1
                break
    return count > 0 and matched == set(roots)


def import_archives(entries, bindings, config_dir, folders, seven_zip, api, progress, cancel):
    """Recover exact file IDs only after local payload and Nexus archive hashes match."""
    game = game_directory(config_dir)
    found, metadata, ambiguous, pages = {}, {}, set(), {}
    archives = sorted({p for folder in folders if folder.is_dir() for p in folder.iterdir()
                       if p.is_file() and p.suffix.casefold() in ('.zip', '.7z', '.rar') and guess_mod_id(p.name)})
    targets = [e for e in entries if not bindings.get(e.key) or bindings[e.key].get('inventory_date') != e.date]
    def normalized(value):
        return re.sub('[^a-z]', '', value.casefold()).removeprefix('mod')
    hints = {normalized(value)[:10] for e in targets for value in [e.name] + [n for _, n in e.components] if len(normalized(value)) >= 8}
    archives = [p for p in archives if any(hint in normalized(p.name) for hint in hints)]
    for index, archive in enumerate(archives):
        if cancel.is_set():
            break
        progress(index, len(archives), 'Matching existing archives')
        try:
            members = archive_members(archive, seven_zip)
            roots = {PurePosixPath(p).name.casefold() for p in archive_layout(members, inspection_only=True)}
            candidates = [e for e in targets if {n.casefold() for t, n in e.components if t in ('data', 'dlc')} & roots]
            if not candidates:
                continue
            mod_id = guess_mod_id(archive.name)
            if mod_id not in metadata:
                metadata[mod_id] = api.load_mod(mod_id)
            files = metadata[mod_id][1]
            digest = file_digest(archive)
            exact = [f for f in files if expected_digest(f) == digest]
            if len(exact) > 1:
                named = [f for f in exact if f.get('file_name') == archive.name]
                if len(named) == 1:
                    exact = named
            if len(exact) != 1:
                if exact:
                    for entry in candidates:
                        pages.setdefault(entry.key, set()).add(mod_id)
                continue
            for entry in candidates:
                pages.setdefault(entry.key, set()).add(mod_id)
                if archive_matches(entry, members, archive, game, seven_zip):
                    binding = binding_for(entry, mod_id, exact[0], evidence='Nexus archive hash and installed payload matched')
                    if entry.key in found and found[entry.key]['file_id'] != binding['file_id']:
                        ambiguous.add(entry.key)
                    found[entry.key] = binding
        except (BridgeError, OSError, subprocess.SubprocessError):
            continue
    result = {key: value for key, value in found.items() if key not in ambiguous}
    for entry in targets:
        if entry.key not in result and len(pages.get(entry.key, set())) == 1:
            mod_id = next(iter(pages[entry.key]))
            result[entry.key] = dict(mod_id=mod_id, file_id=None, name=metadata[mod_id][0].get('name', ''),
                version='', inventory_date=entry.date, evidence='Archive identified; installed payload differs or the version is ambiguous')
    return result, metadata

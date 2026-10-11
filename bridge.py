"""A native Nexus companion with an embedded W3MM installer."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from bridge_core import BridgeError, ModLink, NexusBridge, file_upload_date, parse_link, safe_archive_name, file_digest, expected_digest
from inventory import (archive_layout, archive_matches, archive_members, binding_for, from_model, inspect_install_archive,
                       game_directory, import_archives, library_path, read_inventory, read_library,
                       save_library, update_status)

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
USER_DIR = Path(os.environ['LOCALAPPDATA']) / 'NexusW3MMBridge'
PREFS_PATH = USER_DIR / 'settings.json'
DOWNLOADS = USER_DIR / 'downloads'
SERVER_NAME = 'NexusW3MMBridge-' + hashlib.sha256(str(USER_DIR).lower().encode()).hexdigest()[:16]


def read_prefs():
    for file in (PREFS_PATH, ROOT / 'defaults.json'):
        try:
            return json.loads(file.read_text('utf-8-sig'))
        except (OSError, ValueError):
            pass
    return {}


def save_prefs(prefs):
    USER_DIR.mkdir(parents=True, exist_ok=True)
    temporary = PREFS_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(prefs, indent=2), encoding='utf-8')
    temporary.replace(PREFS_PATH)


def launch_args():
    return [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]


def running_game():
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq witcher3.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    if result.returncode:
        raise BridgeError('Could not check whether the game is running. Close the game and retry.')
    return 'witcher3.exe' in result.stdout.lower()


def protocol_command():
    return subprocess.list2cmdline(launch_args()) + ' "%1"'


def protocol_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\nxm\shell\open\command') as key:
            return winreg.QueryValueEx(key, '')[0] == protocol_command()
    except OSError:
        return False


def set_protocol(enabled, prefs):
    import winreg
    key_path = r'Software\Classes\nxm\shell\open\command'
    if enabled:
        if protocol_enabled():
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            previous = winreg.QueryValueEx(key, '')[0]
        # The current machine uses Vortex. Preserve that exact registration for undo.
        if 'Vortex.exe' not in previous:
            raise BridgeError('Your Nexus handler is not Vortex. Keep using paste-a-link mode on this setup.')
        prefs['previous_nxm_command'] = previous
        save_prefs(prefs)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, protocol_command())
    else:
        if not protocol_enabled():
            return
        previous = prefs.get('previous_nxm_command')
        if not previous:
            raise BridgeError('The previous Nexus handler is unavailable; re-register Vortex in its settings.')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, previous)


def forward_other_game(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'nxm' or parsed.hostname == 'witcher3':
        return False
    vortex = Path(os.environ.get('PROGRAMFILES', r'C:\Program Files')) / 'Black Tree Gaming Ltd/Vortex/Vortex.exe'
    if not vortex.is_file() or len(url) > 8192 or any(c in url for c in '\r\n\x00'):
        raise BridgeError('Cannot forward this Nexus link to Vortex.')
    subprocess.Popen([str(vortex), '-d', url], shell=False)
    return True


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object, object, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.fn(self)
            self.done.emit(result)
        except BridgeError as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit('The request could not be completed. Check Vortex and retry.')


class Manager:
    """Use upstream W3MM's model and UI, under its existing inventory lock."""
    def __init__(self, app, config_dir):
        from src.configuration.config import Configuration
        from src.core.model import Model
        from src.globals import data
        from src.gui.main_widget import CustomMainWidget
        from src.gui.main_window import CustomMainWindow
        from src.gui.themes import get_dark_palette, get_light_palette

        config_dir = Path(config_dir).resolve()
        if not (config_dir / 'config.ini').is_file():
            raise BridgeError('Choose your W3MM executable in Settings first.')
        data.app = app
        data.debug = False
        data.dark_palette, data.light_palette = get_dark_palette(), get_light_palette()
        data.config = Configuration(configPath=str(config_dir))
        try:
            self.model = Model()
        except OSError:
            raise BridgeError('Close the original Witcher 3 Mod Manager, then click Open in W3MM again. Your download is saved.') from None
        # Configuration and inventory backup before this companion can write either.
        backup = USER_DIR / 'backups' / time.strftime('%Y%m%d-%H%M%S')
        backup.mkdir(parents=True, exist_ok=True)
        for name in ('config.ini', 'installed.xml'):
            if (config_dir / name).is_file():
                shutil.copy2(config_dir / name, backup / name)
        self.window = CustomMainWindow()
        self.widget = CustomMainWidget(self.window, self.model)
        self.window.dropCallback = self.install
        self.window.setWindowTitle('The Witcher 3 Mod Manager · Nexus Bridge')
        self.window.setWindowIcon(QIcon(str(ROOT / 'res/w3a.ico')))
        self.config = data.config
        app.aboutToQuit.connect(self.save)

    def show(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def install(self, files):
        if running_game():
            QMessageBox.information(self.window, 'Close The Witcher 3', 'Exit the game before installing mods. Your archive is saved in the bridge; open it again after exiting.')
            return
        self.show()
        return self.widget.installModFiles(files)

    def save(self):
        self.config.saveWindowSettings(self.widget, self.window)
        self.config.write_priority().join()
        self.config.write_config().join()
        self.model.write()


STYLE = '''
QWidget#bridgeRoot { background: #171819; color: #eeeeeb; }
QWidget#bridgeRoot QLabel { color: #eeeeeb; background: transparent; }
QLabel#eyebrow { color: #c7a86b; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 30px; font-weight: 600; }
QLabel#muted, QLabel#status { color: #a7aaa9; }
QLabel#modTitle { font-size: 20px; font-weight: 600; }
QLineEdit { background: #252728; color: #eee; border: 1px solid #414444; border-radius: 6px; padding: 9px 12px; selection-background-color: #765d30; }
QLineEdit:focus { border: 1px solid #cfaf73; }
QPushButton { background: #292b2c; color: #e6e7e3; border: 1px solid #444747; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #353837; }
QPushButton:disabled { color: #707472; border-color: #343737; }
QPushButton#primary { background: #d4b377; color: #1b1b19; border: 1px solid #d4b377; font-weight: 600; }
QPushButton#primary:hover { background: #e1c58f; }
QPushButton#primary:disabled { background: #534a39; border-color: #534a39; color: #9a927f; }
QPushButton:focus { border-color: #e2c188; }
QTableWidget { background: #1c1e1e; alternate-background-color: #222424; color: #e1e2df; border: 1px solid #363a39; border-radius: 5px; gridline-color: #303333; selection-background-color: #464334; selection-color: #fff; }
QHeaderView::section { background: #272a29; color: #acb0ac; border: 0; padding: 9px; font-size: 11px; }
QTableWidget::item { padding: 7px; }
QCheckBox { color: #a7aaa9; spacing: 6px; }
QProgressBar { border: 0; border-radius: 3px; background: #303333; height: 6px; }
QProgressBar::chunk { background: #d4b377; border-radius: 3px; }
QFrame#divider { background: #343737; max-height: 1px; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #252728; color: #aaa; padding: 10px 20px; margin-right: 6px; }
QTabBar::tab:selected { color: #e1c58f; border-bottom: 2px solid #d4b377; }
'''


class BridgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.prefs = read_prefs()
        self.api = NexusBridge()
        self.manager = None
        self.job = None
        self.workers = set()
        self.link = None
        self.files = []
        self.archive = None
        self.auto_file = None
        self.queued_links = []
        self.installing = False
        self.entries = []
        self.bindings = {}
        self.nexus_cache = read_library(USER_DIR / 'nexus-cache.json')
        self.inventory_snapshot = None
        self.tracking_entry = None
        self.archive_source = None
        self.review_file_id = None
        self.check_errors = set()
        self.setWindowTitle('Nexus → W3MM')
        self.setWindowIcon(QIcon(str(ROOT / 'res/w3a.ico')))
        self.resize(920, 730)
        self.setMinimumSize(760, 610)
        root = QWidget(objectName='bridgeRoot')
        root.setStyleSheet(STYLE)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 26, 30, 22)
        layout.setSpacing(16)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(QLabel('THE WITCHER 3  /  MOD COMPANION', objectName='eyebrow'))
        heading.addWidget(QLabel('Nexus → W3MM', objectName='title'))
        top.addLayout(heading)
        top.addStretch()
        settings = QPushButton('Settings')
        menu = QMenu(settings)
        action = menu.addAction('Choose W3MM installation…')
        action.triggered.connect(self.choose_manager)
        self.protocol_action = menu.addAction('Use Nexus download buttons')
        self.protocol_action.setCheckable(True)
        self.protocol_action.setChecked(protocol_enabled())
        self.protocol_action.triggered.connect(self.toggle_protocol)
        menu.addAction('Reconnect to Vortex', self.connect_account)
        settings.setMenu(menu)
        top.addWidget(settings)
        open_manager = QPushButton('Open W3MM')
        open_manager.clicked.connect(lambda: self.open_manager())
        top.addWidget(open_manager)
        layout.addLayout(top)
        self.connection = QLabel('Connecting to your Nexus session…', objectName='muted')
        layout.addWidget(self.connection)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.library_page = QWidget()
        self.tabs.addTab(self.library_page, 'Installed mods')
        self.build_library_page()
        self.files_page = QWidget()
        self.tabs.addTab(self.files_page, 'Nexus files')
        outer_layout = layout
        layout = QVBoxLayout(self.files_page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(QLabel('Nexus mod link'))
        entry = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText('https://www.nexusmods.com/witcher3/mods/…')
        self.url.setAccessibleName('Nexus mod link')
        self.url.returnPressed.connect(self.load_link)
        entry.addWidget(self.url)
        self.find = QPushButton('Find files')
        self.find.clicked.connect(self.load_link)
        entry.addWidget(self.find)
        layout.addLayout(entry)
        divider = QFrame(objectName='divider')
        layout.addWidget(divider)
        self.mod_title = QLabel('Your next mod starts here.', objectName='modTitle')
        self.mod_title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.mod_title)
        self.summary = QLabel('Paste a Nexus link, choose a version, and let W3MM handle the installation.', objectName='muted')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(QLabel('AVAILABLE FILES', objectName='eyebrow'))
        row.addStretch()
        self.old = QCheckBox('Show old versions')
        self.old.toggled.connect(self.render_files)
        row.addWidget(self.old)
        layout.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['FILE', 'VERSION', 'UPLOADED', 'TYPE', 'SIZE'])
        self.table.horizontalHeaderItem(2).setToolTip('Nexus file upload date in your local timezone.')
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 85), (2, 110), (3, 100), (4, 80)):
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setAccessibleName('Available Nexus files')
        self.table.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.table, 1)
        self.detail = QLabel('Main files are shown first. Choose the version that matches your game.', objectName='muted')
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        self.website = QPushButton('View on Nexus')
        self.website.clicked.connect(self.open_nexus)
        actions.addWidget(self.website)
        folder = QPushButton('Downloads')
        folder.clicked.connect(self.open_downloads)
        actions.addWidget(folder)
        self.track_button = QPushButton('Track as installed')
        self.track_button.setToolTip('Associate this exact Nexus file with the selected W3MM entry; does not install anything.')
        self.track_button.clicked.connect(self.track_selected)
        self.track_button.hide()
        actions.addWidget(self.track_button)
        actions.addStretch()
        self.reopen = QPushButton('Open in W3MM')
        self.reopen.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.link_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.track_button.setEnabled(False)
        self.reopen.clicked.connect(self.handoff)
        actions.addWidget(self.reopen)
        self.download_button = QPushButton('Download && open in W3MM', objectName='primary')
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download)
        actions.addWidget(self.download_button)
        layout.addLayout(actions)
        layout = outer_layout
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.status = QLabel('Ready when you are.', objectName='status')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bottom.addWidget(self.status, 1)
        self.cancel_button = QPushButton('Cancel download')
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel_download)
        bottom.addWidget(self.cancel_button)
        layout.addLayout(bottom)
        QTimer.singleShot(0, self.connect_account)
        self.sync_inventory(force=True)
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.sync_inventory)
        self.sync_timer.start(2000)

    def build_library_page(self):
        layout = QVBoxLayout(self.library_page)
        layout.setContentsMargins(0, 16, 0, 0)
        header = QHBoxLayout()
        self.library_summary = QLabel('Reading your W3MM inventory…', objectName='modTitle')
        header.addWidget(self.library_summary, 1)
        self.refresh_button = QPushButton('Refresh && check updates', objectName='primary')
        self.refresh_button.clicked.connect(self.refresh_updates)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.library_origin = QLabel('', objectName='muted')
        self.library_origin.setTextFormat(Qt.TextFormat.PlainText)
        self.library_origin.setWordWrap(True)
        layout.addWidget(self.library_origin)
        self.installed_table = QTableWidget(0, 4)
        self.installed_table.setHorizontalHeaderLabels(['INSTALLED IN W3MM', 'STATE', 'NEXUS VERSION', 'UPDATE CHECK'])
        self.installed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 85), (2, 120), (3, 240)):
            self.installed_table.setColumnWidth(column, width)
        self.installed_table.verticalHeader().hide()
        self.installed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.installed_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.installed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.installed_table.setAlternatingRowColors(True)
        self.installed_table.setShowGrid(False)
        self.installed_table.itemSelectionChanged.connect(self.library_selection_changed)
        self.installed_table.itemDoubleClicked.connect(lambda _: self.review_installed())
        layout.addWidget(self.installed_table, 1)
        self.library_detail = QLabel('Select a mod to review its files or link its Nexus page.', objectName='muted')
        self.library_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.library_detail.setWordWrap(True)
        layout.addWidget(self.library_detail)
        actions = QHBoxLayout()
        self.import_button = QPushButton('Match existing archives')
        self.import_button.clicked.connect(self.match_archives)
        actions.addWidget(self.import_button)
        self.link_button = QPushButton('Link / change Nexus file')
        self.link_button.clicked.connect(self.link_installed)
        actions.addWidget(self.link_button)
        actions.addStretch()
        self.review_button = QPushButton('Review files / update', objectName='primary')
        self.review_button.clicked.connect(self.review_installed)
        actions.addWidget(self.review_button)
        layout.addLayout(actions)

    def selected_entry(self):
        item = self.installed_table.item(self.installed_table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def sync_inventory(self, force=False):
        folder = self.prefs.get('manager_config_dir')
        if not folder:
            self.library_origin.setText('Choose your existing W3MM executable in Settings to show installed mods.')
            return
        try:
            entries = from_model(self.manager.model) if self.manager else read_inventory(Path(folder))
        except BridgeError as error:
            self.library_origin.setText(str(error))
            return
        snapshot = (folder, tuple(entries))
        if snapshot == self.inventory_snapshot and not force:
            return
        previous = self.selected_entry()
        self.inventory_snapshot = snapshot
        self.entries = entries
        self.library_file = library_path(USER_DIR, Path(folder))
        self.bindings = read_library(self.library_file)
        self.library_summary.setText(f'{len(entries)} mods in W3MM')
        self.library_origin.setText('Synced with your W3MM inventory · ' + str(Path(folder) / 'installed.xml'))
        self.render_library(previous.key if previous else None)

    def render_library(self, selected_key=None):
        self.installed_table.setRowCount(0)
        selected_row = 0
        for entry in self.entries:
            row = self.installed_table.rowCount()
            self.installed_table.insertRow(row)
            binding = self.bindings.get(entry.key)
            cache = self.nexus_cache.get(str(binding['mod_id'])) if binding else None
            status, _ = update_status(entry, binding, cache[1] if cache else None)
            if binding and binding['mod_id'] in self.check_errors:
                status = 'Check failed — retry'
            version = binding.get('version') if binding and binding.get('inventory_date') == entry.date else 'Unknown'
            for col, value in enumerate((entry.name, 'Enabled' if entry.enabled else 'Disabled', version or 'Unknown', status)):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setToolTip(str(value))
                self.installed_table.setItem(row, col, item)
            self.installed_table.setRowHeight(row, 40)
            if entry.key == selected_key:
                selected_row = row
        if self.entries:
            self.installed_table.selectRow(selected_row)
        self.library_selection_changed()

    def library_selection_changed(self):
        entry = self.selected_entry()
        self.link_button.setEnabled(entry is not None and self.job is None)
        self.review_button.setEnabled(entry is not None and self.job is None)
        if entry:
            binding = self.bindings.get(entry.key)
            detail = ', '.join(name for _, name in entry.components)
            if binding:
                file_label = f" / file #{binding['file_id']}" if binding.get('file_id') else ''
                detail = f"{binding['name']} · Nexus #{binding['mod_id']}{file_label} · {binding.get('evidence', '')}\n" + detail
            self.library_detail.setText(detail)

    def refresh_updates(self):
        if self.job or self.installing:
            return
        self.sync_inventory(force=True)
        ids = sorted({self.bindings[e.key]['mod_id'] for e in self.entries if e.key in self.bindings})
        if not ids:
            self.match_archives()
            return
        self.status.setText('Checking the exact Nexus files linked to your installed mods…')
        def check(worker):
            cache, errors = {}, set()
            for index, mod_id in enumerate(ids):
                if worker.cancel.is_set():
                    break
                worker.progress.emit(index, len(ids), 'Checking installed mods')
                try:
                    cache[str(mod_id)] = self.api.load_mod(mod_id)
                except BridgeError:
                    errors.add(mod_id)
            return cache, errors
        def checked(result):
            cache, self.check_errors = result
            self.nexus_cache.update(cache)
            save_library(USER_DIR / 'nexus-cache.json', self.nexus_cache)
            self.render_library()
            self.progress.setValue(100)
            self.status.setText(f'Checked {len(cache)} Nexus pages.' + (f' {len(self.check_errors)} failed; retry when connected.' if self.check_errors else ' Select a mod to review its files.'))
        self.work(check, checked)

    def match_archives(self):
        if self.job or not self.entries:
            return
        folder = Path(self.prefs['manager_config_dir'])
        dirs = [Path(os.environ['APPDATA']) / 'Vortex/downloads/witcher3', Path.home() / 'Downloads']
        config = configparser.ConfigParser(interpolation=None)
        config.read(folder / 'config.ini', encoding='utf-8-sig')
        if config.get('PATHS', 'lastpath', fallback=''):
            dirs.append(Path(config.get('PATHS', 'lastpath')))
        entries = list(self.entries)
        self.status.setText('Matching Nexus archives against your installed mod files…')
        def matched(result):
            found, cache = result
            self.sync_inventory(force=True)
            active = {e.key: e for e in self.entries}
            valid = {key: b for key, b in found.items() if key in active and active[key].date == b['inventory_date']}
            self.bindings.update(valid)
            save_library(self.library_file, self.bindings)
            self.nexus_cache.update({str(k): v for k, v in cache.items()})
            save_library(USER_DIR / 'nexus-cache.json', self.nexus_cache)
            self.render_library()
            self.progress.setValue(100)
            exact = sum(bool(b.get('file_id')) for b in valid.values())
            self.status.setText(f'Linked {len(valid)} mods to Nexus; {exact} exact versions verified. Review entries with unknown versions. No game files changed.')
        self.work(lambda w: import_archives(entries, dict(self.bindings), folder, dirs, ROOT / 'tools/7zip/7z.exe', self.api, w.progress.emit, w.cancel), matched, download=True)

    def link_installed(self):
        entry = self.selected_entry()
        if not entry or self.job:
            return
        value, ok = QInputDialog.getText(self, 'Link installed mod to Nexus', f'Paste the Nexus page for {entry.name}:')
        if ok and value.strip():
            try:
                link = parse_link(value)
            except (BridgeError, ValueError) as error:
                self.status.setText(str(error))
                return
            self.tracking_entry = entry
            self.review_file_id = None
            self.tabs.setCurrentWidget(self.files_page)
            self.url.setText(f'https://www.nexusmods.com/witcher3/mods/{link.mod_id}')
            self.load_link(link, tracking=True)

    def review_installed(self):
        entry = self.selected_entry()
        if not entry or self.job:
            return
        binding = self.bindings.get(entry.key)
        if not binding:
            self.link_installed()
            return
        cache = self.nexus_cache.get(str(binding['mod_id']))
        _, suggestion = update_status(entry, binding, cache[1] if cache else None)
        self.tracking_entry = entry
        self.review_file_id = suggestion or binding['file_id']
        self.url.setText(f"https://www.nexusmods.com/witcher3/mods/{binding['mod_id']}")
        self.tabs.setCurrentWidget(self.files_page)
        self.load_link(ModLink(binding['mod_id']), tracking=True)

    def track_selected(self):
        entry, file = self.tracking_entry, self.selected()
        if not entry or not file or self.job or not self.link:
            return
        self.sync_inventory(force=True)
        if not any(e == entry for e in self.entries):
            self.status.setText('That entry changed in W3MM. Select it again in Installed mods.')
            return
        answer = QMessageBox.question(self, 'Confirm installed file', f'You are identifying the file already installed for:\n{entry.name}\n\n{file.get("name")} · {file.get("version")} · file #{file["file_id"]}\n\nThis changes tracking only. Is this the file you installed?')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.bindings[entry.key] = binding_for(entry, self.link.mod_id, file)
        save_library(self.library_file, self.bindings)
        self.render_library(entry.key)
        self.status.setText('Installed file linked. Refresh will check its Nexus page for newer files.')
        self.tabs.setCurrentWidget(self.library_page)

    def work(self, fn, callback, *, download=False):
        if self.job is not None:
            return
        self.job = Worker(fn)
        worker = self.job
        self.workers.add(worker)
        self.find.setEnabled(False)
        self.table.setEnabled(False)
        self.old.setEnabled(False)
        self.url.setEnabled(False)
        self.download_button.setEnabled(False)
        self.reopen.setEnabled(False)
        self.cancel_button.setVisible(download)
        for button in (self.refresh_button, self.import_button, self.link_button, self.review_button, self.track_button):
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)

        def finish():
            self.job = None
            self.find.setEnabled(True)
            self.table.setEnabled(True)
            self.old.setEnabled(True)
            self.url.setEnabled(True)
            self.cancel_button.hide()
            self.progress.setRange(0, 100)
            self.selection_changed()
            self.reopen.setEnabled(self.archive is not None)
            self.refresh_button.setEnabled(True)
            self.import_button.setEnabled(True)
            self.track_button.setEnabled(True)
            self.library_selection_changed()

        def success(value):
            finish()
            callback(value)

        def failed(message):
            finish()
            self.progress.setValue(0)
            self.status.setText(message)

        worker.done.connect(success)
        worker.failed.connect(failed)
        worker.progress.connect(self.show_progress)
        worker.finished.connect(lambda: self.workers.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def connect_account(self):
        if self.job:
            return
        self.connection.setText('Connecting to your Nexus session…')

        def connected(account):
            if account.get('authenticated'):
                self.connection.setText('● Connected through Vortex' + ('  ·  Nexus Premium' if account.get('premium') else '  ·  Use Nexus download buttons for free downloads'))
            else:
                self.connection.setText('Sign in to Nexus in Vortex, then reconnect.')
            if self.queued_links:
                QTimer.singleShot(0, lambda: self.receive_link(self.queued_links.pop(0)))
            elif account.get('authenticated') and self.entries and not self.bindings:
                QTimer.singleShot(0, self.match_archives)

        self.work(lambda w: self.api.rpc('nexus_account'), connected)

    def receive_link(self, value):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if not value:
            return
        if self.job is not None or self.installing:
            self.queued_links.append(value)
            self.status.setText('Another Nexus link is waiting. Finish the current download first.')
            return
        try:
            link = parse_link(value)
        except (BridgeError, ValueError) as error:
            self.status.setText(str(error))
            return
        # Access keys remain in memory only; show the public mod URL.
        self.url.setText(f'https://www.nexusmods.com/witcher3/mods/{link.mod_id}')
        self.tabs.setCurrentWidget(self.files_page)
        self.load_link(link, automatic=value.startswith('nxm:'))

    def load_link(self, link=None, automatic=False, tracking=False):
        if self.job is not None:
            return
        try:
            if not isinstance(link, ModLink):
                link = parse_link(self.url.text())
            self.link = link
            if not tracking:
                self.tracking_entry = None
                self.review_file_id = None
            self.track_button.setVisible(self.tracking_entry is not None)
            self.auto_file = link.file_id if automatic else None
            self.archive = None
            self.archive_source = None
            self.status.setText('Fetching available versions from Nexus…')
            self.work(lambda w: self.api.load_mod(link.mod_id), self.loaded)
        except (BridgeError, ValueError) as error:
            self.status.setText(str(error))

    def loaded(self, result):
        metadata, self.files = result
        self.nexus_cache[str(self.link.mod_id)] = result
        save_library(USER_DIR / 'nexus-cache.json', self.nexus_cache)
        self.mod_title.setText(metadata.get('name') or 'Nexus mod')
        summary = re.sub(r'<[^>]+>', '', metadata.get('summary') or '')
        self.summary.setText(summary[:310])
        self.render_files()
        self.status.setText('Choose a file, then download and open it in W3MM.')
        if self.review_file_id:
            target = next((f for f in self.files if f.get('file_id') == self.review_file_id), None)
            if target and target.get('category_id') in (4, 6, 7):
                self.old.setChecked(True)
            self.select_file(self.review_file_id)
            self.status.setText('Review the selected file and its requirements. Optional files and alternative variants are separate choices.')
        if self.auto_file:
            target = next((f for f in self.files if f.get('file_id') == self.auto_file), None)
            if target:
                if target.get('category_id') in (4, 6, 7):
                    self.old.setChecked(True)
                self.select_file(self.auto_file)
                QTimer.singleShot(0, self.download)
            else:
                self.status.setText('This file is no longer available. Choose a current version.')

    def render_files(self):
        files = [f for f in self.files if self.old.isChecked() or f.get('category_id') not in (4, 6, 7)]
        files.sort(key=lambda f: (not bool(f.get('is_primary')), f.get('category_id') != 1, -int(f.get('uploaded_timestamp') or 0)))
        self.table.setRowCount(0)
        for file in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            size = (file.get('size_in_bytes') or (file.get('size_kb') or 0) * 1024) / 1048576
            uploaded, uploaded_detail = file_upload_date(file)
            values = [file.get('name', 'File'), file.get('version', ''), uploaded, (file.get('category_name') or '').replace('_', ' ').title(), f'{size:.1f} MB']
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, file)
                item.setToolTip(uploaded_detail if column == 2 else str(value))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 39)
        if files:
            self.table.selectRow(0)
            if self.link and self.link.file_id:
                self.select_file(self.link.file_id)
        else:
            self.detail.setText('No current files found. Try showing old versions or open the Nexus page.')
        self.selection_changed()

    def select_file(self, file_id):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole).get('file_id') == file_id:
                self.table.selectRow(row)
                return

    def selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selection_changed(self):
        file = self.selected()
        self.download_button.setEnabled(file is not None and self.job is None)
        if self.job is None:
            self.archive = None
            self.archive_source = None
            if file and self.link:
                try:
                    candidate = DOWNLOADS / str(self.link.mod_id) / str(int(file['file_id'])) / safe_archive_name(file['file_name'])
                    if candidate.is_file():
                        self.archive = candidate
                        self.archive_source = (self.link.mod_id, dict(file))
                except (BridgeError, KeyError, ValueError, OSError):
                    pass
            self.reopen.setEnabled(self.archive is not None)
        if file:
            detail = file.get('changelog_html') or file.get('description') or 'Read the mod description for requirements and compatibility.'
            installed = [e for e in self.entries if self.bindings.get(e.key, {}).get('mod_id') == (self.link.mod_id if self.link else None)
                         and self.bindings.get(e.key, {}).get('file_id') == file.get('file_id')]
            prefix = 'Tracked in W3MM · ' if installed else ''
            self.detail.setText(prefix + re.sub(r'<[^>]+>', ' ', detail)[:350])

    def download(self):
        if self.job is not None or not self.link or not self.selected():
            return
        file, link = self.selected(), self.link
        self.archive = None
        self.archive_source = (link.mod_id, dict(file))
        self.status.setText('Requesting a download from your Nexus session…')
        self.work(lambda w: self.api.download(link, file, DOWNLOADS, w.progress.emit, w.cancel), self.downloaded, download=True)

    def show_progress(self, received, total, text):
        self.progress.setRange(0, 100 if total else 0)
        if total:
            self.progress.setValue(min(100, int(received * 100 / total)))
        if text in ('Checking installed mods', 'Matching existing archives'):
            self.status.setText(f'{text} · {received} / {total}')
        else:
            self.status.setText(f'{text}  ·  {received / 1048576:.1f}' + (f' / {total / 1048576:.1f} MB' if total else ' MB'))

    def downloaded(self, path):
        self.archive = Path(path)
        self.reopen.setEnabled(True)
        self.progress.setValue(100)
        self.status.setText('Download ready. Opening the W3MM installer…')
        QTimer.singleShot(0, self.handoff)

    def cancel_download(self):
        if self.job:
            self.job.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText('Canceling after the current network read…')

    def choose_manager(self):
        if self.manager:
            QMessageBox.information(self, 'Restart to change manager', 'Restart the bridge before choosing a different W3MM installation.')
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose TheWitcher3ModManager.exe', self.prefs.get('manager_config_dir', ''), 'W3MM executable (*.exe)')
        if filename:
            folder = Path(filename).parent
            if not (folder / 'config.ini').is_file():
                QMessageBox.information(self, 'Configuration not found', 'Choose the W3MM installation that contains your config.ini and installed.xml.')
                return
            self.prefs['manager_config_dir'] = str(folder)
            save_prefs(self.prefs)
            self.inventory_snapshot = None
            self.sync_inventory(force=True)
            self.status.setText('W3MM installation saved.')

    def open_manager(self):
        try:
            if self.manager is None:
                folder = self.prefs.get('manager_config_dir')
                if not folder:
                    self.choose_manager()
                    folder = self.prefs.get('manager_config_dir')
                if not folder:
                    return None
                self.manager = Manager(QApplication.instance(), folder)
            self.manager.show()
            return self.manager
        except BridgeError as error:
            self.status.setText(str(error))
            QMessageBox.information(self, 'W3MM handoff', str(error))
        except Exception:
            self.status.setText('W3MM could not open. Your archive is saved. Check the configured installation.')
        return None

    def handoff(self):
        if not self.archive or self.job or self.installing:
            return
        archive, source = self.archive, self.archive_source
        def inspect(worker):
            if not archive.is_file():
                raise BridgeError('The saved archive is missing. Download this file again.')
            if source:
                size = int(source[1].get('size_in_bytes') or 0)
                digest = expected_digest(source[1])
                if ((size and archive.stat().st_size != size) or (digest and file_digest(archive) != digest)):
                    raise BridgeError('The saved archive failed verification. Download this file again.')
            return inspect_install_archive(archive, ROOT / 'tools/7zip/7z.exe')
        def ready(result):
            self.archive, self.archive_source = archive, source
            self.install_prepared(result)
        self.status.setText('Verifying the saved archive and its installer instructions…')
        self.work(inspect, ready)

    def install_prepared(self, inspected):
        try:
            if running_game():
                self.status.setText('Downloaded. Exit The Witcher 3, then click Open in W3MM to install.')
                return
            members, folders = inspected
            if len(folders) > 1:
                answer = QMessageBox.question(self, 'Review archive components',
                    'W3MM will install every component in this archive:\n\n' + '\n'.join(folders[:25]) +
                    '\n\nIf these are alternative variants, cancel and follow the author’s manual instructions. Install these components?')
                if answer != QMessageBox.StandardButton.Yes:
                    self.status.setText('Archive saved. Installation canceled; no installed version was changed.')
                    return
            manager = self.open_manager()
            if manager:
                self.installing = True
                try:
                    outcome = manager.install([str(self.archive)])
                finally:
                    self.installing = False
                self.sync_inventory(force=True)
                if outcome and outcome[0] > 0 and outcome[1] == 0 and outcome[2] == 0 and self.archive_source:
                    self.verify_install(members)
                else:
                    self.status.setText('Review W3MM’s result. Tracking was not advanced for a skipped, failed, or incomplete install.')
        except BridgeError as error:
            self.status.setText(str(error))
        if self.queued_links:
            QTimer.singleShot(0, lambda: self.receive_link(self.queued_links.pop(0)))

    def verify_install(self, members):
        from src.domain.mod import Mod
        archive, source = self.archive, self.archive_source
        entries = [e for e in self.entries if e.name == Mod.formatName(archive.name)]
        folder = Path(self.prefs['manager_config_dir'])
        def verify(worker):
            return [e for e in entries if archive_matches(e, members, archive, game_directory(folder), ROOT / 'tools/7zip/7z.exe')]
        def verified(matches):
            self.sync_inventory(force=True)
            for entry in matches:
                if entry in self.entries:
                    self.bindings[entry.key] = binding_for(entry, source[0], source[1], evidence='W3MM install completed; installed payload matched')
            save_library(self.library_file, self.bindings)
            self.render_library()
            self.status.setText('Installed and tracked. Run Script Merger for affected conflicts.' if matches else 'W3MM finished. Version could not be verified; link the installed file manually.')
        self.work(verify, verified)

    def toggle_protocol(self, enabled):
        try:
            set_protocol(enabled, self.prefs)
            self.status.setText('Nexus download buttons now open this bridge. Other games still go to Vortex.' if enabled else 'Nexus download buttons restored to Vortex.')
        except (BridgeError, OSError) as error:
            self.status.setText(str(error))
        self.protocol_action.setChecked(protocol_enabled())

    def open_nexus(self):
        if self.link:
            QDesktopServices.openUrl(QUrl(f'https://www.nexusmods.com/witcher3/mods/{self.link.mod_id}?tab=files'))
        else:
            QDesktopServices.openUrl(QUrl('https://www.nexusmods.com/witcher3'))

    def open_downloads(self):
        try:
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            os.startfile(str(DOWNLOADS.resolve()), 'explore')
        except OSError as error:
            self.status.setText(f'Could not open Downloads: {error.strerror or error}. Folder: {DOWNLOADS}')

    def closeEvent(self, event):
        if self.workers:
            self.status.setText('Wait for the current request, or cancel the download before closing.')
            event.ignore()
            return
        if self.manager:
            self.manager.window.close()
        event.accept()


def main():
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    from src.gui.themes import get_dark_palette
    app.setPalette(get_dark_palette())
    argument = sys.argv[1] if len(sys.argv) > 1 else ''
    if argument in {'--register', '--unregister'}:
        set_protocol(argument == '--register', read_prefs())
        return 0
    if argument and forward_other_game(argument):
        return 0
    if argument:
        try:
            parse_link(argument)
        except (BridgeError, ValueError):
            QMessageBox.information(None, 'Invalid Nexus link', 'This bridge accepts Witcher 3 Nexus links.')
            return 1
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(500):
        socket.write(json.dumps({'link': argument}).encode() + b'\n')
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(SERVER_NAME)
    if not server.listen(SERVER_NAME):
        QMessageBox.information(None, 'Bridge already opening', 'Wait a moment, then open the bridge again.')
        return 1
    window = BridgeWindow()
    connections = set()

    def accept():
        client = server.nextPendingConnection()
        connections.add(client)
        buffer = bytearray()

        def read():
            buffer.extend(bytes(client.readAll()))
            if len(buffer) > 16384:
                client.abort()
                return
            if b'\n' in buffer:
                try:
                    value = json.loads(bytes(buffer).split(b'\n')[0]).get('link', '')
                    if isinstance(value, str):
                        window.receive_link(value)
                except (ValueError, TypeError, AttributeError):
                    pass
                client.disconnectFromServer()

        client.readyRead.connect(read)
        client.disconnected.connect(lambda: connections.discard(client))
        client.disconnected.connect(client.deleteLater)
        QTimer.singleShot(5000, lambda: client.abort() if client in connections else None)
        read()

    server.newConnection.connect(accept)
    window.show()
    if argument:
        window.queued_links.append(argument)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())

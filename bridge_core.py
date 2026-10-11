"""Nexus downloads through the user's authenticated, local Vortex bridge."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class BridgeError(Exception):
    pass


def file_upload_date(file: dict) -> tuple[str, str]:
    """Nexus file upload time, rendered in the user's local timezone."""
    try:
        timestamp = int(file.get('uploaded_timestamp') or 0)
        if timestamp > 0:
            uploaded = datetime.fromtimestamp(timestamp, timezone.utc)
        else:
            uploaded = datetime.fromisoformat(str(file.get('uploaded_time') or '').replace('Z', '+00:00'))
            if uploaded.tzinfo is None:
                uploaded = uploaded.replace(tzinfo=timezone.utc)
        local = uploaded.astimezone()
        return local.strftime('%Y-%m-%d'), ('Uploaded to Nexus: ' + local.strftime('%Y-%m-%d %H:%M:%S %Z (UTC%z)')
                                         + '\n' + uploaded.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'))
    except (ValueError, TypeError, OverflowError, OSError):
        return 'Unknown', 'Nexus did not provide a valid upload date for this file.'


@dataclass(frozen=True)
class ModLink:
    mod_id: int
    file_id: int | None = None
    key: str | None = None
    expires: int | None = None


def parse_link(value: str) -> ModLink:
    value = value.strip()
    if value.isdigit() and int(value) > 0:
        return ModLink(int(value))
    if len(value) > 8192 or any(c in value for c in '\r\n\x00'):
        raise BridgeError('Invalid Nexus link.')
    url = urllib.parse.urlsplit(value)
    if url.username or url.password or url.port:
        raise BridgeError('Use a Witcher 3 link from nexusmods.com.')
    query = urllib.parse.parse_qs(url.query)
    if url.scheme == 'nxm' and url.hostname == 'witcher3':
        match = re.fullmatch(r'/mods/([1-9]\d*)/files/([1-9]\d*)/?', url.path)
        if match:
            try:
                expires = int(query['expires'][0]) if 'expires' in query else None
            except ValueError:
                raise BridgeError('The Nexus download link has an invalid expiry.') from None
            return ModLink(int(match[1]), int(match[2]), query.get('key', [None])[0], expires)
    if url.scheme == 'https' and url.hostname in {'nexusmods.com', 'www.nexusmods.com'}:
        match = re.fullmatch(r'/witcher3/mods/([1-9]\d*)/?', url.path)
        if match:
            file_id = query.get('file_id', [None])[0]
            if file_id is not None and not re.fullmatch(r'[1-9]\d*', file_id):
                raise BridgeError('Invalid file ID.')
            return ModLink(int(match[1]), int(file_id) if file_id else None)
    raise BridgeError('Paste a Witcher 3 Nexus mod URL, a mod ID, or an nxm download link.')


def safe_archive_name(name: str) -> str:
    if (not name or len(name) > 220 or name != Path(name).name
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((' ', '.'))
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}
            or Path(name).suffix.lower() not in {'.zip', '.7z', '.rar'}):
        raise BridgeError('This Nexus file is not a supported ZIP, 7z, or RAR archive.')
    return name


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def expected_digest(file: dict) -> str | None:
    match = re.search(r'/file/([a-fA-F0-9]{64})(?:/|$)', file.get('external_virus_scan_url') or '')
    return match[1].lower() if match else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise BridgeError('The download redirected to an insecure address.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NexusBridge:
    OPERATIONS = {'nexus_account', 'nexus_mod', 'nexus_mod_files', 'nexus_download_links'}

    def __init__(self, connection_file: Path | None = None):
        self.connection_file = connection_file or Path(os.environ['APPDATA']) / 'Vortex/nexusmods-mcp/bridge.json'

    def rpc(self, operation: str, **params):
        if operation not in self.OPERATIONS:
            raise BridgeError('Unsupported operation.')
        try:
            connection = json.loads(self.connection_file.read_text('utf-8'))
            port, token = connection['port'], connection['token']
            if type(port) is not int or not 1 <= port <= 65535 or not re.fullmatch(r'[a-f0-9]{64}', token):
                raise ValueError('Invalid connection')
        except (OSError, ValueError, KeyError, TypeError):
            raise BridgeError('Open Vortex with the Nexus Mods MCP Bridge extension enabled, then reconnect.') from None
        request = urllib.request.Request(
            f'http://127.0.0.1:{port}/rpc',
            json.dumps({'operation': operation, 'input': params}).encode(),
            {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
        try:
            # Neither a proxy nor an HTTP redirect may receive the local session token.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise BridgeError('Nexus access was denied. Reconnect in Vortex, or use the website download button for this file.') from None
            raise BridgeError(f'Nexus could not complete the request (HTTP {error.code}). Check Vortex and try again.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise BridgeError('Cannot reach Vortex. Open it, sign in to Nexus, then reconnect.') from None
        if 'error' in result:
            raise BridgeError('Nexus rejected the request. Check your Vortex connection and the file on Nexus.')
        return result['result']

    def load_mod(self, mod_id: int) -> tuple[dict, list[dict]]:
        metadata = self.rpc('nexus_mod', game='witcher3', modId=mod_id)
        files, offset = [], 0
        for _ in range(100):
            page = self.rpc('nexus_mod_files', game='witcher3', modId=mod_id, offset=offset, limit=100)
            files.extend(page.get('items', []))
            if page.get('nextOffset') is None:
                break
            offset = page['nextOffset']
        return metadata.get('data', metadata), files

    def download(self, link: ModLink, file: dict, destination: Path, progress, cancel: threading.Event) -> Path:
        name = safe_archive_name(file['file_name'])
        file_id = int(file['file_id'])
        folder = destination.resolve() / str(link.mod_id) / str(file_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        size = int(file.get('size_in_bytes') or 0)
        digest = expected_digest(file)
        if target.is_file() and size > 0 and target.stat().st_size == size and digest and file_digest(target) == digest:
            progress(size, size, 'Using verified download')
            return target
        params = {'game': 'witcher3', 'modId': link.mod_id, 'fileId': file_id}
        if link.file_id == file_id and link.key and link.expires:
            params.update(key=link.key, expires=link.expires)
        payload = self.rpc('nexus_download_links', **params)
        links = payload.get('data', payload.get('links', [])) if isinstance(payload, dict) else payload
        if isinstance(links, dict):
            links = links.get('links', [])
        candidates = [entry.get('URI') or entry.get('uri') or entry.get('url') for entry in links if isinstance(entry, dict)]
        url = next((value for value in candidates if value and urllib.parse.urlsplit(value).scheme == 'https'), None)
        if not url:
            raise BridgeError('No download link was available. Use Mod Manager Download on the Nexus file page.')
        temporary = target.with_name(target.name + '.part')
        received = 0
        hasher = hashlib.sha256()
        try:
            opener = urllib.request.build_opener(HTTPSRedirect())
            request = urllib.request.Request(url, headers={'User-Agent': 'NexusW3MMBridge/1.0'})
            with opener.open(request, timeout=45) as response, temporary.open('wb') as stream:
                total = size or int(response.headers.get('Content-Length', '0'))
                while True:
                    if cancel.is_set():
                        raise BridgeError('Download canceled. No mod files were installed.')
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    stream.write(chunk)
                    hasher.update(chunk)
                    progress(received, total, 'Downloading')
                stream.flush()
                os.fsync(stream.fileno())
            if cancel.is_set():
                raise BridgeError('Download canceled. No mod files were installed.')
            if total and received != total:
                raise BridgeError('The download was incomplete. Please retry.')
            if digest and hasher.hexdigest() != digest:
                raise BridgeError('Download verification failed. Please retry.')
            temporary.replace(target)
            progress(received, total, 'Download verified' if digest else 'Download complete')
            return target
        except BridgeError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # Signed URLs must never be included in logs or dialogs.
            raise BridgeError('Download failed. Check your connection and retry; Nexus links can expire.') from None
        finally:
            temporary.unlink(missing_ok=True)

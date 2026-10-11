# Nexus → W3MM

A Windows companion for downloading Witcher 3 mods from Nexus and updating them through The Witcher 3 Mod Manager. The Installed tab follows your existing W3MM inventory, with file-specific Nexus version tracking.

## Get started

1. Download the Windows ZIP from [Releases](https://github.com/iheanyi/nexus-w3mm-bridge/releases), extract the entire folder, and run `NexusW3MMBridge.exe`.
2. Keep [Vortex](https://www.nexusmods.com/about/vortex/) running and signed into Nexus, with the [Nexus Mods MCP Bridge extension](https://github.com/iheanyi/nexusmods-mcp) enabled. Follow that project's extension installation instructions. This companion reuses its local authenticated connection; no API key is entered here.
3. Choose **Settings → Choose W3MM installation**, then select your existing `TheWitcher3ModManager.exe`. Its directory must contain `config.ini` and `installed.xml`.
4. Your installed mods appear immediately, even without a Nexus connection. The list follows W3MM changes every two seconds. **Refresh & check updates** reads the inventory again and checks linked Nexus pages.
5. Select a mod and choose **Review files / update**. Choose the intended archive, read its requirements, then **Download & open in W3MM**. Close the game before installation and run Script Merger for affected conflicts afterward.

The original W3MM and the companion's embedded manager share the same inventory lock. Close the original manager before opening the embedded one. Reading the Installed tab does not acquire that lock or change W3MM files.

Keep the W3MM window opened by the bridge running while downloading more mods; subsequent installs reuse it. The original standalone executable is a separate process, so it cannot own the inventory at the same time.

## Updating without duplicate entries

After a complete installation passes payload verification, the bridge retires a previous main-package entry when it belongs to the same Nexus page, owns exactly the same mod/DLC folders, and has no extra components. The new entry keeps ownership of the installed files. Archived versions require starting from **Installed mods → Review files / update** so the intended previous entry is explicit. Optional files, disabled alternatives, unrelated pages, and partly overlapping bundles are kept for review.

Cleanup changes the inventory only; it never calls Uninstall. A copy of `installed.xml` is saved in `backups/before-update-cleanup-*` first. Canceled, skipped, failed, incomplete, or unverified updates do not retire the old entry. W3MM replaces a selected mod folder as a whole, so obsolete files inside that folder are removed by the normal overwrite operation.

Do not uninstall an older duplicate to tidy the list: it may own the same files as the new entry. The bridge's bundled W3MM now refuses an uninstall when another entry shares those files. This protection does not modify the original standalone W3MM executable. The Installed tab shows **Files missing — reinstall** when a recorded mod/DLC folder is absent; reinstall its intended archive to restore it.

## Recover tracking for mods you already installed

W3MM's inventory does not store Nexus mod IDs, file IDs, or structured versions. **Match existing archives** inspects identifiable Nexus archives in Vortex's Witcher 3 downloads, your Downloads folder, and W3MM's last-used folder. It checks the archive's published Nexus SHA-256 and compares its mod/DLC payload to installed files. This matching is read-only.

An exact match records that specific file ID. If an archive identifies the page but the installed payload differs, the page is linked while the installed version remains unknown. Multiple matching versions are also left unresolved. Renamed archives without recognizable Nexus IDs may need manual linking.

For an unmatched entry, use **Link / change Nexus file**, paste its Nexus page, select the exact file you installed (enable **Show old versions** when needed), and click **Track as installed**. This is a tracking declaration, not an installation. Changed W3MM installation dates invalidate the recorded version. Removed entries disappear from the list; enabling or disabling an entry preserves its association.

## Multiple files, optional files, and variants

- Every Nexus archive is listed separately with its version, local upload date, and Main / Optional / Update / Miscellaneous category. Hover over the date for the exact local and UTC timestamps. Old and archived files are hidden until you enable **Show old versions**.
- Install a main archive and any desired optional archives individually. Each W3MM inventory record is linked to its own Nexus file ID. Optional files are never automatically added, and the page's latest version is not assigned to every component.
- **Newer file — review** is a suggestion based on an exact file-name match and upload time. Nexus's local bridge does not expose authoritative file replacement chains. A changed name, a different category, or multiple candidates is shown as **New files — choose variant**. Neither status guarantees game-version compatibility.
- Updates always require a file choice. There is no unattended “update all” operation. A Classic / Next-Gen / Remastered switch or an optional compatibility patch needs your review.
- A single archive can contain several mod or DLC folders. The bridge lists them and asks before handing all of them to W3MM. Simple FOMOD configurations that only copy the standard Mods, DLC, or bin folders are supported. Duplicate alternative folders and FOMOD installers with choices, conditions, or custom destinations require manual installation. Arbitrary custom installer formats cannot be inferred reliably.
- Previously downloaded archives can be opened again after restarting the bridge; their size and published hash are rechecked before installation. A download is not an installation. Tracking advances only after W3MM reports a complete installation and the installed payload matches. Skipped, failed, incomplete, or unverified installations do not become confirmed versions. Menu-only packages can be linked manually.

## Nexus download buttons

**Settings → Use Nexus download buttons** registers a per-user `nxm:` handler. Witcher 3 links go here; other games go to Vortex. Disable the setting to restore the previous Vortex command. Keep the app folder in place while it is registered. The existing handler must be Vortex.

Free accounts should use Nexus's **Mod Manager Download** button, which supplies a short-lived download key. Premium accounts can request the selected file directly. Normal Nexus account restrictions apply. Free-account URL handling is covered by tests; the live download test used a Premium account.

## Local data

Stored under `%LOCALAPPDATA%\NexusW3MMBridge`:

| Location | Contents |
| --- | --- |
| `settings.json` | W3MM location and previous protocol handler |
| `library-*.json` | Nexus associations scoped to a W3MM installation |
| `nexus-cache.json` | Public Nexus metadata from the last check |
| `downloads/` | Downloaded archives, grouped by mod ID and file ID |
| `backups/` | W3MM config and inventory snapshots before the embedded manager opens |

These backups do not include game saves or every installed mod file. The app does not write to Vortex's installed-mod list. Session tokens and free download keys are used in memory and excluded from saved metadata. Downloads use partial files, size verification, and Nexus's published SHA-256 when available.

## Develop and build

Windows 10/11, Python 3.11 or newer. The packaged application is unsigned.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe bridge.py
.\build.ps1
```

The build is written to `dist/NexusW3MMBridge`. `build.ps1 -Python <path> -OutputDirectory <path>` supports an existing environment and custom output directory. Keep the directory layout intact. No private config or machine-specific default is included in the source or release.

`bridge_core.py` handles Nexus and downloads; `inventory.py` reads W3MM and matches archives; `bridge.py` contains the Qt interface and installer integration. Tests cover inventory changes, variant selection, archive guards, cancellation, and download verification.

## Credits and licenses

Bridge code: [MIT](LICENSE.txt). Bundled W3MM: [BSD-2-Clause](W3MM-LICENSE.txt), based on [Systemcluster/The-Witcher-3-Mod-manager](https://github.com/Systemcluster/The-Witcher-3-Mod-manager), version 0.10.5, commit `010334e134fb6cc172a28ba76e7cf5c14117c20d`. Integration changes expose installation/save results, exclude FOMOD metadata from the game-menu scanner, keep skipped payloads from claiming a complete new version, and protect files shared by multiple inventory records during uninstall.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled tools and runtime licenses. This is an independent companion, unaffiliated with Nexus Mods or CD PROJEKT RED.

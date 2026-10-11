# Nexus → W3MM

A Windows companion for downloading Witcher 3 mods from Nexus and updating them through The Witcher 3 Mod Manager. The Installed tab follows your existing W3MM inventory, with file-specific Nexus version tracking.

## Get started

1. Download the Windows ZIP from [Releases](https://github.com/iheanyi/nexus-w3mm-bridge/releases), extract the entire folder, and run `NexusW3MMBridge.exe`.
2. Keep [Vortex](https://www.nexusmods.com/about/vortex/) running and signed into Nexus, with the [Nexus Mods MCP Bridge extension](https://github.com/iheanyi/nexusmods-mcp) enabled. Follow that project's extension installation instructions. This companion reuses its local authenticated connection; no API key is entered here.
3. Choose **Settings → Choose W3MM installation**, then select your existing `TheWitcher3ModManager.exe`, `installed.xml`, or `config.ini`. The bridge checks beside the executable and the standard Windows user folders. If several inventories exist, choose yours; for a custom location, select its inventory file. The inventory folder must contain both `config.ini` and `installed.xml`. Open and configure W3MM first on a new installation.
4. Your installed mods appear immediately, even without a Nexus connection. The list follows W3MM changes every two seconds. **Refresh & check updates** reads the inventory again and checks linked Nexus pages.
5. Select a mod and choose **Review files / update**. Choose the intended archive, read its requirements, then **Download & open in W3MM**. Close the game before installation and run Script Merger for affected conflicts afterward.

Startup shows the local inventory and saved Nexus metadata immediately. The account status check runs separately in the background with a five-second network timeout; it does not disable inventory actions or start an archive scan. Use **Match existing archives** when you want to recover file associations. **Refresh & check updates** explicitly requests fresh online metadata. A Nexus download link received at launch starts independently of the account status check.

The original W3MM and the companion's embedded manager share the same inventory lock. Close the original manager before opening the embedded one. Reading the Installed tab does not acquire that lock or change W3MM files.

Keep the W3MM window opened by the bridge running while downloading more mods; subsequent installs reuse it. The original standalone executable is a separate process, so it cannot own the inventory at the same time.

## Using the ZIP on another computer

The release is a self-contained **Windows x64** application. Extract the whole folder on each PC; Python, a developer checkout, and Codex are not required. macOS, Linux/Steam Deck, and native ARM64 packages are not provided. The application is unsigned.

Each Windows account keeps its own settings and downloads under its Local AppData folder. The ZIP contains no author's inventory, account token, game path, or downloaded mods. On a new PC, sign into that PC's Vortex bridge and choose that PC's W3MM inventory. This does not migrate game files or installed mods between PCs. Game and manager folders must be writable by your account; the bridge does not request administrator privileges.

Vortex and its Nexus Mods MCP Bridge extension are required for Nexus access; they are not bundled. The installed-mod list remains usable offline. Windows known-folder locations support redirected Documents and Downloads, including OneDrive. Custom Vortex download locations can be matched via W3MM's last-used folder; archives outside the searched locations can be linked manually.

You can move the extracted application folder. If you enabled Nexus download buttons, enable that setting again from the new location to refresh its per-user registration. Forwarding to other games preserves the original Vortex executable path and arguments, including custom and per-user installations.

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
- Update checks compare the installed variant's file name with its own release label removed, preserving variant names and game-patch numbers. Later uploads of other loot variants, optional files, or compatibility patches do not mark the installed variant outdated. **Newer version — review** requires a higher dotted numeric version in the matching variant/category. A different file with the same version is **Re-uploaded file — review**, unless its published hash is identical. Unknown version formats and multiple matching candidates require review. Nexus's local bridge does not expose authoritative replacement chains, so a renamed or archived branch may need manual review; these suggestions do not guarantee game-version compatibility.
- Updates always require a file choice. There is no unattended “update all” operation. A Classic / Next-Gen / Remastered switch or an optional compatibility patch needs your review.
- A single archive can contain several mod or DLC folders. The bridge lists them and asks before handing all of them to W3MM. Simple FOMOD configurations that only copy the standard Mods, DLC, or bin folders are supported. Duplicate alternative folders and FOMOD installers with choices, conditions, or custom destinations require manual installation. Arbitrary custom installer formats cannot be inferred reliably.
- Previously downloaded archives can be opened again after restarting the bridge; their size and published hash are rechecked before installation. A download is not an installation. Tracking advances only after W3MM reports a complete installation and the installed payload matches. Skipped, failed, incomplete, or unverified installations do not become confirmed versions. Menu-only packages can be linked manually.

## Nexus download buttons

**Settings → Use Nexus download buttons** registers a per-user `nxm:` handler. Witcher 3 links go here; other games go to the registered Vortex installation. Disable the setting to restore the previous Vortex command. Register Nexus links in Vortex first if there is no Vortex handler. After moving the bridge folder, re-enable this setting from its new location.

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

Windows x64, Python 3.11 (the version used for release builds). Build and run from a writable directory.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe bridge.py
.\build.ps1
```

The build is written to `dist/NexusW3MMBridge`. `build.ps1 -Python <path> -OutputDirectory <path>` supports an existing environment and custom output directory. Keep the directory layout intact. No private config or machine-specific default is included in the source or release.

Every build runs the unit/integration tests and `verify_package.py`. That check rejects private state in the package, copies the release to a path containing spaces and Unicode, and launches the EXE from a different working directory with a fresh profile and a Windows-only PATH. It checks the Qt window, embedded W3MM, local settings, bundled assets, and 7-Zip extraction without connecting to Nexus or touching a real game. CI builds and runs these checks on both `windows-2022` and `windows-latest`; downloadable build artifacts are attached to successful runs. This validates clean Windows environments, not every hardware/OS combination.

`bridge_core.py` handles Nexus and downloads; `inventory.py` reads W3MM and matches archives; `bridge.py` contains the Qt interface and installer integration; `windows_setup.py` resolves user folders, manager inventories, and Vortex registrations. Tests cover inventory changes, variant selection, archive guards, cancellation, download verification, and portable Windows setup.

## Credits and licenses

Bridge code: [MIT](LICENSE.txt). Bundled W3MM: [BSD-2-Clause](W3MM-LICENSE.txt), based on [Systemcluster/The-Witcher-3-Mod-manager](https://github.com/Systemcluster/The-Witcher-3-Mod-manager), version 0.10.5, commit `010334e134fb6cc172a28ba76e7cf5c14117c20d`. Integration changes expose installation/save results, exclude FOMOD metadata from the game-menu scanner, keep skipped payloads from claiming a complete new version, and protect files shared by multiple inventory records during uninstall.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled tools and runtime licenses. This is an independent companion, unaffiliated with Nexus Mods or CD PROJEKT RED.

# Third-party components

- The Witcher 3 Mod Manager 0.10.5: BSD-2-Clause, copyright Stefan Kostic and Christian Sdunek. Source is included under `src/`; assets under `res/` and `translations/`. See `W3MM-LICENSE.txt` and the upstream commit documented in the README.
- 7-Zip: `tools/7zip/7z.exe` and `7z.dll`, with the distribution's complete license at `tools/7zip/License.txt`. LGPL and additional notices, including the unRAR restriction, apply. Source and build information: https://www.7-zip.org/download.html and https://www.7-zip.org/license.txt. These files can be replaced with compatible builds.
- PySide6 / Qt for Python and Qt: dynamically loaded runtime libraries in the Windows package. License texts are copied from installed distributions into `licenses/`. LGPLv3/GPL and component-specific terms apply to the applicable open-source distribution. Source and build instructions: https://code.qt.io/pyside/pyside-setup.git/ and https://code.qt.io/qt/ . The bridge source and build recipe allow rebuilding and using modified libraries. This project imposes no restriction on reverse engineering for debugging modifications to LGPL libraries.
- Other Python runtime dependency notices, including PyInstaller, fasteners, watchdog, charset-normalizer and pywin32, are copied into the release's `licenses/` directory when supplied by their distributions. Versions are listed in `requirements.txt`.

Nexus archives, user settings, game files and saved games are not distributed with this project. The Nexus Mods MCP Bridge extension is an external prerequisite, not bundled in the package.

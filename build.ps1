param(
    [string]$Python = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist')
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = (Resolve-Path -LiteralPath $Python).Path
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
& $venvPython -m unittest discover -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
$appIcon = Join-Path $PSScriptRoot 'res\w3a.ico'
$buildRoot = Join-Path $PSScriptRoot 'build'
$target = Join-Path $OutputDirectory 'NexusW3MMBridge'
# PyInstaller's safety check uses a string prefix. A sibling source directory
# named NexusW3MMBridge-source must therefore use a separate build directory.
if ($buildRoot.StartsWith($target, [StringComparison]::OrdinalIgnoreCase)) {
    $buildRoot = Join-Path ([IO.Path]::GetTempPath()) 'nexus-w3mm-build'
}
& $venvPython -m PyInstaller --noconfirm --windowed --onedir --name NexusW3MMBridge --icon $appIcon --distpath $OutputDirectory --workpath (Join-Path $buildRoot 'pyinstaller') --specpath $buildRoot bridge.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$target = Join-Path $OutputDirectory 'NexusW3MMBridge'
# Use the machine's Windows API-set/UCRT/ICU libraries. PyInstaller's copied
# system forwarders can resolve to the wrong exports under its DLL search path.
$runtimeRoot = (Resolve-Path -LiteralPath (Join-Path $target '_internal')).Path
$osCopies = @(Get-ChildItem -LiteralPath $runtimeRoot -File | Where-Object { $_.Name -match '^api-ms-.*\.dll$|^ucrtbase\.dll$|^icuuc\.dll$|^icudt78\.dll$' })
foreach ($osCopy in $osCopies) {
    if ($osCopy.DirectoryName -ne $runtimeRoot) { throw 'Unexpected DLL directory' }
    Remove-Item -LiteralPath $osCopy.FullName
}
$qtRuntime = (& $venvPython -c 'import pathlib, PySide6; print(pathlib.Path(PySide6.__file__).parent)').Trim()
Copy-Item -LiteralPath (Join-Path $qtRuntime 'VCRUNTIME140.dll'),(Join-Path $qtRuntime 'VCRUNTIME140_1.dll') -Destination $runtimeRoot -Force
Copy-Item -LiteralPath res,translations,tools -Destination $target -Recurse -Force
Copy-Item -LiteralPath defaults.json,W3MM-LICENSE.txt,LICENSE.txt,README.txt,README.md,THIRD_PARTY_NOTICES.md -Destination $target -Force
$sitePackages = (& $venvPython -c 'import sysconfig; print(sysconfig.get_path("purelib"))').Trim()
$licenseTarget = Join-Path $target 'licenses'
New-Item -ItemType Directory -Path $licenseTarget -Force | Out-Null
Get-ChildItem -LiteralPath $sitePackages -Directory -Filter '*.dist-info' | ForEach-Object {
    $infoDir = $_
    $files = Get-ChildItem -LiteralPath $infoDir.FullName -Recurse -File | Where-Object { $_.Name -match 'LICENSE|COPYING|NOTICE' }
    if ($files) {
        $dest = Join-Path $licenseTarget $infoDir.Name
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        $files | Copy-Item -Destination $dest -Force
    }
}

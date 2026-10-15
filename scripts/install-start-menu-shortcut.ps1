[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$launcherPath = Join-Path $RepoRoot 'LocalVoiceBridge.exe'
if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "Launcher not found: $launcherPath"
}

$programsFolder = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
if ([string]::IsNullOrWhiteSpace($programsFolder)) {
    throw 'Windows Start Menu Programs folder could not be resolved.'
}

$shortcutPath = Join-Path $programsFolder 'Local Voice Bridge.lnk'
$legacyShortcutPath = Join-Path $programsFolder 'ChatGPT Local Voice Bridge.lnk'
if (Test-Path -LiteralPath $legacyShortcutPath -PathType Leaf) {
    Remove-Item -LiteralPath $legacyShortcutPath -Force
}
$wshShell = $null
$shortcut = $null
try {
    $wshShell = New-Object -ComObject WScript.Shell
    $shortcut = $wshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcherPath
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.IconLocation = "$launcherPath,0"
    $shortcut.Description = 'Start Local Voice Bridge'
    $shortcut.Save()
}
finally {
    if ($null -ne $shortcut) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
    }
    if ($null -ne $wshShell) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wshShell)
    }
}

if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
    throw "Start Menu shortcut was not created: $shortcutPath"
}

Write-Host "Start Menu shortcut: $shortcutPath"

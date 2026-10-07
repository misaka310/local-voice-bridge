[CmdletBinding()]
param(
    [switch]$RemoveGeneratedData
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$localApi = Join-Path $repoRoot 'local-api'
$runtimeDir = Join-Path $localApi 'runtime'
$audioDir = Join-Path $runtimeDir 'audio'
$logsDir = Join-Path $localApi 'logs'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$valueNames = @('Local Voice Bridge', 'ChatGPT Local Voice Bridge')
$removed = [System.Collections.Generic.List[string]]::new()

Start-Sleep -Seconds 2

foreach ($valueName in $valueNames) {
    try {
        Remove-ItemProperty -Path $runKey -Name $valueName -Force -ErrorAction Stop
        $removed.Add("startup:$valueName")
    }
    catch [System.Management.Automation.PSArgumentException] {}
    catch [System.Management.Automation.ItemNotFoundException] {}
}

$programsFolder = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
foreach ($name in @('Local Voice Bridge.lnk', 'ChatGPT Local Voice Bridge.lnk')) {
    $path = Join-Path $programsFolder $name
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Remove-Item -LiteralPath $path -Force
        $removed.Add("shortcut:$name")
    }
}

$appData = [Environment]::GetFolderPath([Environment+SpecialFolder]::ApplicationData)
$startupFolder = Join-Path $appData 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupEntryNames = @(
    'Local Voice Bridge.lnk',
    'ChatGPT Local Voice Bridge.lnk',
    'Local Voice Bridge.vbs',
    'ChatGPT Local Voice Bridge.vbs'
)
foreach ($name in $startupEntryNames) {
    $path = Join-Path $startupFolder $name
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Remove-Item -LiteralPath $path -Force
        $removed.Add("startup-folder:$name")
    }
}

if ($RemoveGeneratedData) {
    if (Test-Path -LiteralPath $audioDir -PathType Container) {
        Get-ChildItem -LiteralPath $audioDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension.ToLowerInvariant() -in @('.wav', '.flac', '.mp3', '.ogg', '.m4a', '.aac') } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $removed.Add('generated-audio')
    }
    if (Test-Path -LiteralPath $logsDir -PathType Container) {
        Get-ChildItem -LiteralPath $logsDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '*.log*' } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $removed.Add('logs')
    }
    Remove-Item -LiteralPath (Join-Path $runtimeDir 'server-instance.json') -Force -ErrorAction SilentlyContinue
}

$result = [ordered]@{
    ok = $true
    removed = @($removed)
    generatedDataRemoved = [bool]$RemoveGeneratedData
    referenceVoicesPreserved = $true
    settingsPreserved = $true
    environmentPreserved = $true
    completedAt = (Get-Date).ToString('o')
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeDir 'uninstall-result.json') -Encoding UTF8

try {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        "自動起動とスタートメニュー登録を解除しました。`n参照音声、設定、モデル、リポジトリ本体は残っています。",
        'Local Voice Bridge',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    )
}
catch {}

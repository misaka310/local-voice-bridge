param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $PSScriptRoot "launcher\VoiceBridgeLauncher.cs"
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $root "LocalVoiceBridge.exe"
}

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Launcher source was not found: $sourcePath"
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

$source = Get-Content -LiteralPath $sourcePath -Raw -Encoding UTF8
Add-Type `
    -TypeDefinition $source `
    -Language CSharp `
    -ReferencedAssemblies @("System.dll", "System.Windows.Forms.dll") `
    -OutputAssembly $OutputPath `
    -OutputType WindowsApplication

if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "Launcher build did not create: $OutputPath"
}

$size = (Get-Item -LiteralPath $OutputPath).Length
Write-Host "[ok] Built LocalVoiceBridge.exe ($size bytes)"

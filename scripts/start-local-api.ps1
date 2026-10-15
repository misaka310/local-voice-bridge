$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$LocalApiDir = Join-Path $Root "local-api"
$Python = Join-Path $LocalApiDir ".venv\Scripts\python.exe"
$Preflight = Join-Path $LocalApiDir "scripts\preflight_irodori.py"
$Port = 8717

if (!(Test-Path -LiteralPath $Python)) {
  throw "venv python not found: $Python. Run setup-voice-env.cmd first."
}
if (!(Test-Path -LiteralPath $Preflight)) {
  throw "Irodori preflight not found: $Preflight"
}

$owner = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess
if ($owner) {
  $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
  $name = if ($process) { $process.ProcessName } else { "unknown" }
  throw "Port $Port is already in use. PID=$owner Name=$name. Stop that process before starting the local API."
}

$env:LOCAL_VOICE_PORT = "$Port"
$env:LOCAL_VOICE_PUBLIC_BASE_URL = "http://127.0.0.1:$Port"

Write-Host "Checking Irodori v3, CUDA, and model cache..."
$preflightProcess = Start-Process -FilePath $Python -ArgumentList @(
  "-u",
  $Preflight,
  "--strict-cuda",
  "--quick"
) -NoNewWindow -Wait -PassThru
if ($preflightProcess.ExitCode -ne 0) {
  throw "Irodori preflight failed. Run setup-voice-env.cmd first."
}

Write-Host "Local API: http://127.0.0.1:$Port"
Write-Host "Health: http://127.0.0.1:$Port/health"
Write-Host "Keep this window open while using the extension. Closing it stops the local API."

Push-Location $LocalApiDir
try {
  # Run Python as this PowerShell process's direct foreground child.
  # Do not use Start-Process here: it can survive after the terminal is closed.
  & $Python -u "server.py"
  $serverExitCode = $LASTEXITCODE
} finally {
  Pop-Location
}

exit $serverExitCode

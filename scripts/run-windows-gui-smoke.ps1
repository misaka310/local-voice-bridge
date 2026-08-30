param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (
    $env:GITHUB_ACTIONS -ne 'true' -or
    $env:RUNNER_OS -ne 'Windows' -or
    $env:LOCAL_VOICE_GUI_RUNNER -ne 'github-hosted-windows-latest'
) {
    throw 'Windows GUI smoke must run only on GitHub-hosted windows-latest. Do not run it on the user''s everyday Windows desktop.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $repoRoot 'local-api\.venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'
$requirements = Join-Path $repoRoot 'tests\windows\requirements-gui-smoke.txt'
$smokeScript = Join-Path $repoRoot 'tests\windows\hosted_tray_uia_smoke.py'
$launcher = Join-Path $repoRoot 'LocalVoiceBridge.exe'

function Invoke-Process {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $repoRoot -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ne 0) {
        throw "$FailureMessage (exit=$($process.ExitCode))"
    }
}

function Get-PythonVersion {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = '-c "import platform; print(platform.python_version())"'
    $startInfo.WorkingDirectory = $repoRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Could not read the GUI smoke Python version from $FilePath (exit=$($process.ExitCode)): $standardError"
    }
    $version = $standardOutput.Trim()
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "GUI smoke Python returned an empty version string: $FilePath"
    }
    return $version
}

if (-not [Environment]::UserInteractive) {
    throw 'Windows GUI smoke requires a logged-in interactive desktop session.'
}

if (-not (Test-Path $python)) {
    $configuredPython = $null
    if (-not [string]::IsNullOrWhiteSpace($env:pythonLocation)) {
        $candidate = Join-Path $env:pythonLocation 'python.exe'
        if (Test-Path $candidate -PathType Leaf) {
            $configuredPython = $candidate
        }
    }

    if ($null -ne $configuredPython) {
        Invoke-Process -FilePath $configuredPython -Arguments @('-m', 'venv', $venvRoot) -FailureMessage "Failed to create GUI smoke virtual environment: $venvRoot"
    }
    else {
        $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($null -ne $systemPython) {
            Invoke-Process -FilePath $systemPython.Source -Arguments @('-m', 'venv', $venvRoot) -FailureMessage "Failed to create GUI smoke virtual environment: $venvRoot"
        }
        else {
            $py = Get-Command py.exe -ErrorAction Stop
            Invoke-Process -FilePath $py.Source -Arguments @('-3.11', '-m', 'venv', $venvRoot) -FailureMessage "Failed to create GUI smoke virtual environment: $venvRoot"
        }
    }
}

$venvVersion = Get-PythonVersion -FilePath $python
Write-Host "[gui-smoke] Python $venvVersion at $python"
if (-not [string]::IsNullOrWhiteSpace($env:pythonLocation) -and -not $venvVersion.StartsWith('3.11.')) {
    throw "GitHub Actions configured Python 3.11, but the GUI smoke virtual environment uses $venvVersion. Delete $venvRoot and recreate it with the configured interpreter."
}

if (-not $SkipDependencyInstall) {
    Invoke-Process -FilePath $python -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip') -FailureMessage 'Failed to update pip for the GUI smoke environment.'
    Invoke-Process -FilePath $python -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $requirements) -FailureMessage 'Failed to install Windows GUI smoke dependencies.'
}

Push-Location $repoRoot
try {
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    Invoke-Process -FilePath $node -Arguments @((Join-Path $repoRoot 'scripts\run-launcher-build.js')) -FailureMessage 'LocalVoiceBridge.exe could not be built.'
    if (-not (Test-Path $launcher -PathType Leaf)) {
        throw 'LocalVoiceBridge.exe could not be built.'
    }

    Invoke-Process -FilePath $launcher -Arguments @('--self-test') -FailureMessage 'LocalVoiceBridge.exe self-test failed.'

    # The hosted GUI smoke verifies the normal post-onboarding tray contract.
    # First-run onboarding has its own focused Qt regression test and is verified on the real Windows machine.
    $runtimeDir = Join-Path $repoRoot 'local-api\runtime'
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $onboardingState = Join-Path $runtimeDir 'control-panel-onboarding.json'
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($onboardingState, '{"completed":true}', $utf8NoBom)

    $smoke = Start-Process -FilePath $python -ArgumentList @($smokeScript) -WorkingDirectory $repoRoot -Wait -PassThru -NoNewWindow
    exit $smoke.ExitCode
}
finally {
    Pop-Location
}

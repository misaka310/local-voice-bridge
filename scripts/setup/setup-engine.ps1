param(
    [ValidateSet("reading", "stt", "dev")]
    [string]$Profile = "reading",
    [switch]$ResetProgress,
    [switch]$SkipModelDownload,
    [switch]$Describe
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$localApi = Join-Path $repoRoot "local-api"
$venv = Join-Path $localApi ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$pythonw = Join-Path $venv "Scripts\pythonw.exe"
$runtimeSetup = Join-Path $localApi "runtime\setup"
$statePath = Join-Path $runtimeSetup "state.json"
$logPath = Join-Path $runtimeSetup "setup.log"
$failurePath = Join-Path $runtimeSetup "last-failure.json"
$progressPath = Join-Path $runtimeSetup "progress.jsonl"

$profileInfo = @{
    reading = @{
        Title = "読み上げのみ"
        Download = "約8〜14 GB"
        Disk = "約15〜25 GB"
        MinimumFreeGb = 15
        Detail = "Irodori v3、CUDA版PyTorch、TorchCodec、FFmpeg、Windows小窓を導入します。マイク入力用STTは導入しません。"
    }
    stt = @{
        Title = "マイク会話・STT追加"
        Download = "読み上げ環境に加えて約0.2 GB（STTモデルは選択時に別途約0.5〜3 GB）"
        Disk = "読み上げ環境に加えて約1〜4 GB"
        MinimumFreeGb = 18
        Detail = "読み上げ環境を導入または確認したうえで、faster-whisperと録音依存を追加します。"
    }
    dev = @{
        Title = "開発者向け（通常は不要）"
        Download = "完全環境に加えて約0.5〜1.5 GB"
        Disk = "完全環境に加えて約2〜4 GB"
        MinimumFreeGb = 20
        Detail = "読み上げ・STTを導入または確認し、npm依存、Playwright Chromium、Windows GUIスモーク依存も追加します。"
    }
}

if ($Describe) {
    $profileInfo.GetEnumerator() | ForEach-Object {
        [pscustomobject]@{
            id = $_.Key
            title = $_.Value.Title
            download = $_.Value.Download
            disk = $_.Value.Disk
            minimumFreeGb = $_.Value.MinimumFreeGb
            detail = $_.Value.Detail
        }
    } | Sort-Object id | ConvertTo-Json -Depth 4
    exit 0
}

New-Item -ItemType Directory -Path $runtimeSetup -Force | Out-Null
Remove-Item -LiteralPath $progressPath -Force -ErrorAction SilentlyContinue
if ($ResetProgress) {
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $failurePath -Force -ErrorAction SilentlyContinue
}

function Write-SetupLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
}

function Write-StageEvent {
    param(
        [string]$Id,
        [string]$Status,
        [string]$Message,
        [string]$Code = ""
    )
    $safeMessage = ($Message -replace "[\r\n|]", " ").Trim()
    [ordered]@{
        id = $Id
        status = $Status
        message = $safeMessage
        code = $Code
        at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $progressPath -Encoding UTF8
    Write-Output ("LVB_PROGRESS|{0}|{1}|{2}" -f $Id, $Status, $Code)
    Write-SetupLog ("[{0}] {1}: {2} {3}" -f $Status.ToUpperInvariant(), $Id, $safeMessage, $Code)
}

function Load-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return [pscustomobject]@{ version = 1; completed = @(); lastProfile = "" }
    }
    try {
        $loaded = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $completed = @($loaded.completed | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ version = 1; completed = $completed; lastProfile = [string]$loaded.lastProfile }
    } catch {
        Write-SetupLog "State file could not be read and will be replaced: $($_.Exception.Message)"
        return [pscustomobject]@{ version = 1; completed = @(); lastProfile = "" }
    }
}

$script:State = Load-State

function Save-State {
    $payload = [ordered]@{
        version = 1
        completed = @($script:State.completed | Sort-Object -Unique)
        lastProfile = $Profile
        updatedAt = (Get-Date).ToString("o")
    }
    $temporary = "$statePath.tmp"
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Save-Failure {
    param([string]$Id, [string]$Name, [string]$Code, [string]$Message)
    [ordered]@{
        ok = $false
        profile = $Profile
        stageId = $Id
        stageName = $Name
        code = $Code
        message = $Message
        logPath = $logPath
        occurredAt = (Get-Date).ToString("o")
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $failurePath -Encoding UTF8
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    Write-SetupLog ("RUN {0} {1}" -f $FilePath, ($Arguments -join " "))
    & $FilePath @Arguments 2>&1 | ForEach-Object {
        $text = [string]$_
        Write-Output $text
        Write-SetupLog $text
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit=$LASTEXITCODE)"
    }
}

function Test-Native {
    param([string]$FilePath, [string[]]$Arguments)
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { return $false }
    try {
        & $FilePath @Arguments *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-PythonImport {
    param([string]$Imports)
    return Test-Native -FilePath $python -Arguments @("-c", $Imports)
}

function Invoke-SetupStage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [scriptblock]$Verify = { $true },
        [switch]$AlwaysRun
    )

    $wasCompleted = @($script:State.completed) -contains $Id
    if (-not $AlwaysRun -and $wasCompleted) {
        $valid = $false
        try { $valid = [bool](& $Verify) } catch { $valid = $false }
        if ($valid) {
            Write-StageEvent -Id $Id -Status "skipped" -Message "$Name（完了済み）"
            return
        }
        Write-StageEvent -Id $Id -Status "retrying" -Message "$Name（完了記録はありますが再確認に失敗）"
    } else {
        Write-StageEvent -Id $Id -Status "running" -Message $Name
    }

    try {
        & $Action
        if (-not [bool](& $Verify)) {
            throw "工程後の確認に失敗しました。"
        }
        if (-not (@($script:State.completed) -contains $Id)) {
            $script:State.completed = @($script:State.completed) + $Id
        }
        Save-State
        Remove-Item -LiteralPath $failurePath -Force -ErrorAction SilentlyContinue
        Write-StageEvent -Id $Id -Status "passed" -Message $Name
    } catch {
        $message = $_.Exception.Message
        Save-Failure -Id $Id -Name $Name -Code $Code -Message $message
        Write-StageEvent -Id $Id -Status "failed" -Message "${Name}: $message" -Code $Code
        throw [System.InvalidOperationException]::new("$Code ${Name}: $message", $_.Exception)
    }
}

function New-VoiceVenv {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        Invoke-Native -FilePath $py.Source -Arguments @("-3", "-m", "venv", $venv) -FailureMessage "Python仮想環境を作成できませんでした。"
        return
    }
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $systemPython) {
        throw "Python 3.10以上が見つかりません。"
    }
    Invoke-Native -FilePath $systemPython.Source -Arguments @("-m", "venv", $venv) -FailureMessage "Python仮想環境を作成できませんでした。"
}

function Get-VenvBasePython {
    $cfgPath = Join-Path $venv "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfgPath -PathType Leaf)) { return $null }
    $homeLine = Get-Content -LiteralPath $cfgPath | Where-Object { $_ -like "home = *" } | Select-Object -First 1
    if (-not $homeLine) { return $null }
    $home = ($homeLine -replace "^home = ", "").Trim()
    $basePython = Join-Path $home "python.exe"
    if (Test-Path -LiteralPath $basePython -PathType Leaf) { return $basePython }
    return $null
}

function Test-VenvPython {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { return $false }

    $basePython = Get-VenvBasePython
    if ($null -ne $basePython) {
        try {
            $venvHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $python).Hash
            $baseHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $basePython).Hash
            if ($venvHash -eq $baseHash) {
                return $false
            }
        } catch {
            return $false
        }
    }

    return Test-Native -FilePath $python -Arguments @("--version")
}

function Repair-VoiceVenv {
    $basePython = Get-VenvBasePython
    if ($null -eq $basePython -or -not (Test-Native -FilePath $basePython -Arguments @("--version"))) {
        throw "既存のPython仮想環境の元になったPython本体が見つかりません。Python 3.11を修復または再インストールしてから、もう一度セットアップしてください。"
    }
    Invoke-Native -FilePath $basePython -Arguments @("-m", "venv", "--upgrade", $venv) -FailureMessage "Python仮想環境のランチャー修復に失敗しました。"
}

function Get-FreeSpaceGb {
    $rootPath = [System.IO.Path]::GetPathRoot($repoRoot)
    $drive = New-Object System.IO.DriveInfo($rootPath)
    return [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
}

function Test-ReadingEnvironmentReady {
    if (-not (Test-VenvPython)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $localApi "runtime\ffmpeg-shared\bin") -PathType Container)) { return $false }
    if (-not (Test-Path -LiteralPath (Join-Path $venv "Lib\site-packages\local_voice_ffmpeg_bootstrap.pth") -PathType Leaf)) { return $false }
    return Test-PythonImport "import torch, torchaudio, torchcodec, PySide6, irodori"
}

function Get-RequiredFreeSpaceGb {
    param([string]$SelectedProfile)
    if ($SelectedProfile -eq "reading") { return 15.0 }
    if (Test-ReadingEnvironmentReady) {
        if ($SelectedProfile -eq "stt") { return 3.0 }
        if ($SelectedProfile -eq "dev") { return 4.0 }
    }
    return [double]$profileInfo[$SelectedProfile].MinimumFreeGb
}

try {
    Write-SetupLog "==== Setup start profile=$Profile ===="
    $profile = $profileInfo[$Profile]
    $freeGb = Get-FreeSpaceGb
    $requiredFreeGb = Get-RequiredFreeSpaceGb -SelectedProfile $Profile
    $spaceLabel = if ($requiredFreeGb -lt [double]$profile.MinimumFreeGb) { "追加分の最低空き ${requiredFreeGb} GB" } else { "最低空き ${requiredFreeGb} GB" }
    Write-StageEvent -Id "profile" -Status "info" -Message ("{0} / ダウンロード {1} / 必要容量 {2} / {3} / 現在の空き {4} GB" -f $profile.Title, $profile.Download, $profile.Disk, $spaceLabel, $freeGb)

    Invoke-SetupStage -Id "preflight" -Name "Python・空き容量の確認" -Code "LVB-SETUP-001" -AlwaysRun -Action {
        if ($freeGb -lt $requiredFreeGb) {
            throw ("空き容量が不足しています。最低 {0} GB、現在 {1} GBです。" -f $requiredFreeGb, $freeGb)
        }
        if (-not (Get-Command py.exe -ErrorAction SilentlyContinue) -and -not (Get-Command python.exe -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $python)) {
            throw "Python 3.10以上が見つかりません。"
        }
    }

    Invoke-SetupStage -Id "venv" -Name "Python仮想環境の作成・修復" -Code "LVB-SETUP-010" -Verify { Test-VenvPython } -Action {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            New-VoiceVenv
        } elseif (-not (Test-VenvPython)) {
            Repair-VoiceVenv
        }
    }

    Invoke-SetupStage -Id "pip" -Name "pip・setuptools・wheelの更新" -Code "LVB-SETUP-020" -Verify { Test-Native -FilePath $python -Arguments @("-m", "pip", "--version") } -Action {
        Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") -FailureMessage "pip基盤の更新に失敗しました。"
    }

    Invoke-SetupStage -Id "torch" -Name "CUDA版PyTorch・TorchAudioの導入" -Code "LVB-SETUP-040" -Verify { Test-PythonImport "import torch, torchaudio" } -Action {
        Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "torch==2.11.0", "torchaudio==2.11.0", "--index-url", "https://download.pytorch.org/whl/cu128") -FailureMessage "CUDA版PyTorchの導入に失敗しました。"
    }

    Invoke-SetupStage -Id "torchcodec" -Name "TorchCodecの導入" -Code "LVB-SETUP-050" -Verify { Test-PythonImport "import torchcodec" } -Action {
        Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "torchcodec==0.14.0") -FailureMessage "TorchCodecの導入に失敗しました。"
    }

    Invoke-SetupStage -Id "ffmpeg" -Name "共有FFmpegランタイムの取得" -Code "LVB-SETUP-060" -Verify { Test-Path -LiteralPath (Join-Path $localApi "runtime\ffmpeg-shared\bin") -PathType Container } -Action {
        Invoke-Native -FilePath $python -Arguments @((Join-Path $localApi "scripts\ensure_shared_ffmpeg.py")) -FailureMessage "FFmpegランタイムの取得に失敗しました。"
    }

    Invoke-SetupStage -Id "venv-bootstrap" -Name "FFmpeg DLL読み込み設定の導入" -Code "LVB-SETUP-070" -Verify { Test-Path -LiteralPath (Join-Path $venv "Lib\site-packages\local_voice_ffmpeg_bootstrap.pth") -PathType Leaf } -Action {
        Invoke-Native -FilePath $python -Arguments @((Join-Path $localApi "scripts\install_venv_bootstrap.py")) -FailureMessage "仮想環境ブートストラップの導入に失敗しました。"
    }

    Invoke-SetupStage -Id "core-dependencies" -Name "読み上げ・Windows小窓の依存を導入" -Code "LVB-SETUP-080" -Verify { Test-PythonImport "import PySide6, soundfile, transformers, yaml" } -Action {
        Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "--upgrade-strategy", "only-if-needed", "-r", (Join-Path $localApi "requirements-core.txt")) -FailureMessage "読み上げ依存の導入に失敗しました。"
    }

    Invoke-SetupStage -Id "irodori" -Name "Irodoriランタイムの導入" -Code "LVB-SETUP-090" -Verify { Test-PythonImport "import irodori" } -Action {
        Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "--no-deps", "-r", (Join-Path $localApi "requirements-irodori.txt")) -FailureMessage "Irodoriランタイムの導入に失敗しました。"
    }

    Invoke-SetupStage -Id "dependency-audit" -Name "依存バージョンとGitコミットの確認" -Code "LVB-SETUP-095" -AlwaysRun -Action {
        Invoke-Native -FilePath $python -Arguments @((Join-Path $localApi "scripts\audit_runtime_dependencies.py")) -FailureMessage "依存関係の整合性確認に失敗しました。"
    }

    Invoke-SetupStage -Id "runtime-check" -Name "CUDA・音声ランタイムの確認" -Code "LVB-SETUP-100" -AlwaysRun -Action {
        Invoke-Native -FilePath $python -Arguments @((Join-Path $localApi "scripts\preflight_irodori.py"), "--strict-cuda", "--quick") -FailureMessage "CUDAまたは音声ランタイムの確認に失敗しました。"
    }

    if (-not $SkipModelDownload) {
        Invoke-SetupStage -Id "model-cache" -Name "Irodoriモデル・Codecの取得" -Code "LVB-SETUP-110" -AlwaysRun -Action {
            Invoke-Native -FilePath $python -Arguments @((Join-Path $localApi "scripts\preflight_irodori.py"), "--strict-cuda") -FailureMessage "Irodoriモデルの取得または確認に失敗しました。"
        }
    }

    Invoke-SetupStage -Id "runtime-folders" -Name "生成音声フォルダの準備" -Code "LVB-SETUP-120" -Verify { Test-Path -LiteralPath (Join-Path $localApi "runtime\audio") -PathType Container } -Action {
        New-Item -ItemType Directory -Path (Join-Path $localApi "runtime\audio") -Force | Out-Null
    }

    Invoke-SetupStage -Id "launcher" -Name "WindowsランチャーEXEの構築" -Code "LVB-SETUP-130" -AlwaysRun -Verify { Test-Path -LiteralPath (Join-Path $repoRoot "LocalVoiceBridge.exe") -PathType Leaf } -Action {
        $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
        Invoke-Native -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repoRoot "scripts\build-launcher.ps1")) -FailureMessage "Windowsランチャーを構築できませんでした。"
    }

    Invoke-SetupStage -Id "start-menu" -Name "スタートメニューへの登録" -Code "LVB-SETUP-140" -AlwaysRun -Action {
        $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
        Invoke-Native -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repoRoot "scripts\install-start-menu-shortcut.ps1"), "-RepoRoot", $repoRoot) -FailureMessage "スタートメニューへの登録に失敗しました。"
    }

    if ($Profile -in @("stt", "dev")) {
        Invoke-SetupStage -Id "stt-dependencies" -Name "マイク会話・STT依存の導入" -Code "LVB-SETUP-200" -Verify { Test-PythonImport "import faster_whisper, sounddevice" } -Action {
            Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "--upgrade-strategy", "only-if-needed", "-r", (Join-Path $localApi "requirements-stt.txt")) -FailureMessage "STT依存の導入に失敗しました。"
        }
    }

    if ($Profile -eq "dev") {
        Invoke-SetupStage -Id "node-dependencies" -Name "Node.js開発依存の導入" -Code "LVB-SETUP-300" -Verify { Test-Path -LiteralPath (Join-Path $repoRoot "node_modules\@playwright\test") -PathType Container } -Action {
            $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
            Invoke-Native -FilePath $npm -Arguments @("ci") -FailureMessage "npm依存の導入に失敗しました。"
        }
        Invoke-SetupStage -Id "playwright-browser" -Name "Playwright Chromiumの導入" -Code "LVB-SETUP-310" -AlwaysRun -Action {
            $npx = (Get-Command npx.cmd -ErrorAction Stop).Source
            Invoke-Native -FilePath $npx -Arguments @("playwright", "install", "chromium") -FailureMessage "Playwright Chromiumの導入に失敗しました。"
        }
        Invoke-SetupStage -Id "gui-smoke-dependencies" -Name "Windows GUIスモーク依存の導入" -Code "LVB-SETUP-320" -Verify { Test-PythonImport "import pywinauto, psutil, PIL" } -Action {
            Invoke-Native -FilePath $python -Arguments @("-m", "pip", "install", "--upgrade", "-r", (Join-Path $repoRoot "tests\windows\requirements-gui-smoke.txt")) -FailureMessage "Windows GUIスモーク依存の導入に失敗しました。"
        }
    }

    Write-StageEvent -Id "complete" -Status "passed" -Message ("{0}のセットアップが完了しました。" -f $profile.Title)
    Write-SetupLog "==== Setup complete profile=$Profile ===="
    exit 0
} catch {
    Write-SetupLog "==== Setup failed profile=${Profile}: $($_.Exception.Message) ===="
    Write-Output ("LVB_RESULT|FAILED|{0}|{1}" -f $failurePath, $logPath)
    exit 1
}

param(
    [ValidateSet("reading", "stt", "dev")]
    [string]$InitialProfile = "reading"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$engine = Join-Path $PSScriptRoot "setup-engine.ps1"
$launcher = Join-Path $repoRoot "LocalVoiceBridge.exe"
$runtimeSetup = Join-Path $repoRoot "local-api\runtime\setup"
$setupLog = Join-Path $runtimeSetup "setup.log"
$failureJson = Join-Path $runtimeSetup "last-failure.json"
$progressPath = Join-Path $runtimeSetup "progress.jsonl"
$stdoutPath = Join-Path $runtimeSetup "gui-progress.log"
$stderrPath = Join-Path $runtimeSetup "gui-error.log"
$extensionGuide = Join-Path $repoRoot "extension\INSTALL.md"
New-Item -ItemType Directory -Path $runtimeSetup -Force | Out-Null

$profiles = [ordered]@{
    reading = [pscustomobject]@{
        Title = "読み上げのみ"
        Summary = "推奨。Irodori v3、CUDA版PyTorch、FFmpeg、Windows小窓を導入します。STTは導入しません。"
        Download = "推定ダウンロード: 約8〜14 GB"
        Disk = "必要な空き容量: 約15〜25 GB"
    }
    stt = [pscustomobject]@{
        Title = "読み上げ + マイク会話"
        Summary = "読み上げ環境にfaster-whisperと録音依存を追加します。STTモデルは選択時に取得します。"
        Download = "推定ダウンロード: 約8〜17 GB"
        Disk = "必要な空き容量: 約18〜29 GB"
    }
    dev = [pscustomobject]@{
        Title = "開発者向け（通常は不要）"
        Summary = "アプリのソースコードを修正・テストする人向けです。読み上げ・STTに加え、npm、Playwright Chromium、GUIスモーク依存を導入します。"
        Download = "推定ダウンロード: 約9〜19 GB"
        Disk = "必要な空き容量: 約20〜33 GB"
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Local Voice Bridge セットアップ"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(900, 650)
$form.MinimumSize = New-Object System.Drawing.Size(760, 560)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.Icon = [System.Drawing.SystemIcons]::Application

$title = New-Object System.Windows.Forms.Label
$title.Text = "利用する機能を選択してください"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(24, 20)
$form.Controls.Add($title)

$profileLabel = New-Object System.Windows.Forms.Label
$profileLabel.Text = "セットアップ内容"
$profileLabel.AutoSize = $true
$profileLabel.Location = New-Object System.Drawing.Point(27, 65)
$form.Controls.Add($profileLabel)

$profileBox = New-Object System.Windows.Forms.ComboBox
$profileBox.DropDownStyle = "DropDownList"
$profileBox.Location = New-Object System.Drawing.Point(170, 61)
$profileBox.Width = 280
$profileBox.DisplayMember = "Label"
$profileBox.ValueMember = "Id"
$form.Controls.Add($profileBox)

$advancedCheck = New-Object System.Windows.Forms.CheckBox
$advancedCheck.Text = "開発者向けの項目を表示"
$advancedCheck.AutoSize = $true
$advancedCheck.Location = New-Object System.Drawing.Point(470, 64)
$advancedCheck.Checked = ($InitialProfile -eq "dev")
$form.Controls.Add($advancedCheck)

$summary = New-Object System.Windows.Forms.Label
$summary.Location = New-Object System.Drawing.Point(28, 103)
$summary.Size = New-Object System.Drawing.Size(825, 55)
$summary.Anchor = "Top,Left,Right"
$form.Controls.Add($summary)

$estimate = New-Object System.Windows.Forms.Label
$estimate.Location = New-Object System.Drawing.Point(28, 155)
$estimate.Size = New-Object System.Drawing.Size(825, 45)
$estimate.Anchor = "Top,Left,Right"
$estimate.ForeColor = [System.Drawing.Color]::FromArgb(70, 70, 70)
$form.Controls.Add($estimate)

$grid = New-Object System.Windows.Forms.DataGridView
$grid.Location = New-Object System.Drawing.Point(28, 210)
$grid.Size = New-Object System.Drawing.Size(825, 280)
$grid.Anchor = "Top,Bottom,Left,Right"
$grid.AllowUserToAddRows = $false
$grid.AllowUserToDeleteRows = $false
$grid.ReadOnly = $true
$grid.RowHeadersVisible = $false
$grid.AutoSizeColumnsMode = "Fill"
[void]$grid.Columns.Add("Stage", "工程")
[void]$grid.Columns.Add("Status", "状態")
[void]$grid.Columns.Add("Detail", "詳細")
[void]$grid.Columns.Add("Code", "失敗コード")
$grid.Columns[0].FillWeight = 22
$grid.Columns[1].FillWeight = 14
$grid.Columns[2].FillWeight = 49
$grid.Columns[3].FillWeight = 15
$form.Controls.Add($grid)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(28, 500)
$status.Size = New-Object System.Drawing.Size(825, 38)
$status.Anchor = "Bottom,Left,Right"
$status.Text = "未開始"
$form.Controls.Add($status)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "セットアップ開始"
$startButton.Location = New-Object System.Drawing.Point(28, 552)
$startButton.Size = New-Object System.Drawing.Size(145, 38)
$startButton.Anchor = "Bottom,Left"
$form.Controls.Add($startButton)

$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Text = "キャンセル"
$cancelButton.Location = New-Object System.Drawing.Point(185, 552)
$cancelButton.Size = New-Object System.Drawing.Size(120, 38)
$cancelButton.Anchor = "Bottom,Left"
$cancelButton.Enabled = $false
$form.Controls.Add($cancelButton)

$copyButton = New-Object System.Windows.Forms.Button
$copyButton.Text = "失敗内容をコピー"
$copyButton.Location = New-Object System.Drawing.Point(317, 552)
$copyButton.Size = New-Object System.Drawing.Size(145, 38)
$copyButton.Anchor = "Bottom,Left"
$form.Controls.Add($copyButton)

$logButton = New-Object System.Windows.Forms.Button
$logButton.Text = "ログを開く"
$logButton.Location = New-Object System.Drawing.Point(474, 552)
$logButton.Size = New-Object System.Drawing.Size(105, 38)
$logButton.Anchor = "Bottom,Left"
$form.Controls.Add($logButton)

$guideButton = New-Object System.Windows.Forms.Button
$guideButton.Text = "拡張機能の導入手順"
$guideButton.Location = New-Object System.Drawing.Point(591, 552)
$guideButton.Size = New-Object System.Drawing.Size(165, 38)
$guideButton.Anchor = "Bottom,Left"
$form.Controls.Add($guideButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = "閉じる"
$closeButton.Location = New-Object System.Drawing.Point(768, 552)
$closeButton.Size = New-Object System.Drawing.Size(85, 38)
$closeButton.Anchor = "Bottom,Right"
$form.Controls.Add($closeButton)

$script:process = $null
$script:stdoutCount = 0
$script:stderrCount = 0
$script:rowByStage = @{}
$script:activeProfile = $InitialProfile
$script:setupComplete = $false

function Get-SelectedProfile {
    if ($null -ne $profileBox.SelectedItem -and $null -ne $profileBox.SelectedItem.Id) {
        return [string]$profileBox.SelectedItem.Id
    }
    return "reading"
}

function Refresh-ProfileItems {
    param([string]$PreferredProfile = "")

    if ([string]::IsNullOrWhiteSpace($PreferredProfile)) {
        $PreferredProfile = Get-SelectedProfile
    }
    $visibleProfileKeys = @("reading", "stt")
    if ($advancedCheck.Checked) {
        $visibleProfileKeys += "dev"
    }

    $profileBox.BeginUpdate()
    try {
        $profileBox.Items.Clear()
        foreach ($key in $visibleProfileKeys) {
            [void]$profileBox.Items.Add([pscustomobject]@{ Label = $profiles[$key].Title; Id = $key })
        }
        $targetIndex = 0
        for ($i = 0; $i -lt $profileBox.Items.Count; $i++) {
            if ([string]$profileBox.Items[$i].Id -eq $PreferredProfile) {
                $targetIndex = $i
                break
            }
        }
        $profileBox.SelectedIndex = $targetIndex
    } finally {
        $profileBox.EndUpdate()
    }
}

function Update-ProfileDescription {
    $id = Get-SelectedProfile
    $item = $profiles[$id]
    $summary.Text = $item.Summary
    $estimate.Text = "$($item.Download)`r`n$($item.Disk)`r`n完了済み工程は検証後にスキップされ、失敗した工程から再開します。"
}

function Set-StageRow {
    param([string]$Stage, [string]$StageStatus, [string]$Message, [string]$Code)
    if ($Stage -eq "profile") {
        $status.Text = $Message
        return
    }
    if (-not $script:rowByStage.ContainsKey($Stage)) {
        $index = $grid.Rows.Add($Message, $StageStatus, $Message, $Code)
        $script:rowByStage[$Stage] = $index
    }
    $row = $grid.Rows[[int]$script:rowByStage[$Stage]]
    $row.Cells[0].Value = $Stage
    $row.Cells[1].Value = $StageStatus
    $row.Cells[2].Value = $Message
    $row.Cells[3].Value = $Code
    switch ($StageStatus) {
        "failed" { $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::MistyRose }
        "passed" { $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::Honeydew }
        "skipped" { $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::WhiteSmoke }
        default { $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::LightYellow }
    }
    if ($grid.Rows.Count -gt 0) { $grid.FirstDisplayedScrollingRowIndex = $grid.Rows.Count - 1 }
}

function Read-LinesSafe {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    try {
        return @([System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8))
    } catch {
        return @()
    }
}

function Process-ProgressLines {
    $lines = Read-LinesSafe $progressPath
    if ($lines.Count -gt $script:stdoutCount) {
        for ($i = $script:stdoutCount; $i -lt $lines.Count; $i++) {
            try {
                $event = $lines[$i] | ConvertFrom-Json
                Set-StageRow -Stage ([string]$event.id) -StageStatus ([string]$event.status) -Message ([string]$event.message) -Code ([string]$event.code)
                $status.Text = [string]$event.message
            } catch {
                continue
            }
        }
        $script:stdoutCount = $lines.Count
    }
    $errorLines = Read-LinesSafe $stderrPath
    if ($errorLines.Count -gt $script:stderrCount) {
        $script:stderrCount = $errorLines.Count
    }
}

function Start-SetupProcessNoWindow {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = ($Arguments -join " ")
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        $process.Dispose()
        throw "セットアップ処理を開始できませんでした。"
    }
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    return $process
}

function Stop-SetupProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) { return }
    try {
        if ($Process.HasExited) { return }
    } catch {
        return
    }
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $taskkill
    $startInfo.Arguments = "/PID $($Process.Id) /T /F"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $killer = [System.Diagnostics.Process]::Start($startInfo)
    try {
        if (-not $killer.WaitForExit(10000)) {
            try { $killer.Kill() } catch {}
            throw "セットアップのキャンセル処理がタイムアウトしました。"
        }
    } finally {
        $killer.Dispose()
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "セットアップ処理を終了できませんでした。"
    }
}

function Finish-SetupProcess {
    Process-ProgressLines
    $exitCode = $script:process.ExitCode
    if ($exitCode -eq 0) {
        $status.Text = "セットアップが完了しました。次に「拡張機能の導入手順」で導入または再読み込みし、「Local Voice Bridge を開く」を押してください。小窓で接続を待って「テスト音声」を確認します。"
        $status.ForeColor = [System.Drawing.Color]::DarkGreen
        $script:setupComplete = $true
        $startButton.Text = "Local Voice Bridge を開く"
        $startButton.Width = 277
        $cancelButton.Visible = $false
    } else {
        $status.Text = "セットアップに失敗しました。失敗工程を修正後、再実行すると続きから再開します。"
        $status.ForeColor = [System.Drawing.Color]::DarkRed
        $script:setupComplete = $false
        $startButton.Text = "失敗工程から再試行"
        $startButton.Width = 145
        $cancelButton.Visible = $true
    }
    $startButton.Enabled = $true
    $profileBox.Enabled = $true
    $advancedCheck.Enabled = $true
    $closeButton.Enabled = $true
    $cancelButton.Enabled = $false
    $script:process.Dispose()
    $script:process = $null
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if ($null -eq $script:process) { return }
    Process-ProgressLines
    if ($script:process.HasExited) { Finish-SetupProcess }
})

$profileBox.Add_SelectedIndexChanged({ Update-ProfileDescription })
$advancedCheck.Add_CheckedChanged({
    $preferred = Get-SelectedProfile
    if (-not $advancedCheck.Checked -and $preferred -eq "dev") {
        $preferred = "reading"
    }
    Refresh-ProfileItems -PreferredProfile $preferred
    Update-ProfileDescription
})
$startButton.Add_Click({
    if ($null -ne $script:process) { return }
    if ($script:setupComplete) {
        if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
            [System.Windows.Forms.MessageBox]::Show("LocalVoiceBridge.exe が見つかりません。セットアップを再実行してください。", "Local Voice Bridge", "OK", "Warning") | Out-Null
            return
        }
        Start-Process -FilePath $launcher | Out-Null
        $form.Close()
        return
    }
    $script:activeProfile = Get-SelectedProfile
    $grid.Rows.Clear()
    $script:rowByStage = @{}
    $script:stdoutCount = 0
    $script:stderrCount = 0
    Remove-Item -LiteralPath $progressPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    $status.Text = "セットアップを開始しています…"
    $status.ForeColor = [System.Drawing.Color]::Black
    $startButton.Enabled = $false
    $profileBox.Enabled = $false
    $advancedCheck.Enabled = $false
    $closeButton.Enabled = $false
    $cancelButton.Visible = $true
    try {
        $arguments = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", ('"' + $engine + '"'),
            "-Profile", $script:activeProfile
        )
        $script:process = Start-SetupProcessNoWindow -FilePath "powershell.exe" -Arguments $arguments
        $cancelButton.Enabled = $true
        $timer.Start()
    } catch {
        $status.Text = "セットアップ処理を開始できませんでした: $($_.Exception.Message)"
        $status.ForeColor = [System.Drawing.Color]::DarkRed
        $startButton.Enabled = $true
        $profileBox.Enabled = $true
        $advancedCheck.Enabled = $true
        $closeButton.Enabled = $true
        $cancelButton.Enabled = $false
    }
})

$cancelButton.Add_Click({
    if ($null -eq $script:process) { return }
    try {
        if ($script:process.HasExited) { return }
    } catch {
        return
    }
    $cancelButton.Enabled = $false
    $status.Text = "セットアップをキャンセルしています…"
    $status.ForeColor = [System.Drawing.Color]::Black
    try {
        Stop-SetupProcessTree -Process $script:process
        $timer.Stop()
        Process-ProgressLines
        $script:process.Dispose()
        $script:process = $null
        $script:setupComplete = $false
        $status.Text = "セットアップをキャンセルしました。再実行すると完了済み工程を再確認して続きから再開します。"
        $status.ForeColor = [System.Drawing.Color]::DarkGoldenrod
        $startButton.Text = "再確認・再実行"
        $startButton.Width = 145
        $cancelButton.Visible = $true
        $startButton.Enabled = $true
        $profileBox.Enabled = $true
        $advancedCheck.Enabled = $true
        $closeButton.Enabled = $true
    } catch {
        $status.Text = "セットアップのキャンセルに失敗しました: $($_.Exception.Message)"
        $status.ForeColor = [System.Drawing.Color]::DarkRed
        $cancelButton.Enabled = $true
    }
})

$copyButton.Add_Click({
    $parts = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $failureJson -PathType Leaf) {
        $parts.Add((Get-Content -LiteralPath $failureJson -Raw -Encoding UTF8))
    }
    if (Test-Path -LiteralPath $setupLog -PathType Leaf) {
        $tail = Get-Content -LiteralPath $setupLog -Tail 80 -Encoding UTF8
        $parts.Add("--- setup.log tail ---`r`n" + ($tail -join "`r`n"))
    }
    if ($parts.Count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show("コピーできる失敗情報はまだありません。", "Local Voice Bridge", "OK", "Information") | Out-Null
        return
    }
    [System.Windows.Forms.Clipboard]::SetText(($parts -join "`r`n`r`n"))
    $status.Text = "失敗コードとログ末尾をクリップボードへコピーしました。"
})

$logButton.Add_Click({
    if (-not (Test-Path -LiteralPath $setupLog -PathType Leaf)) {
        [System.Windows.Forms.MessageBox]::Show("セットアップログはまだありません。", "Local Voice Bridge", "OK", "Information") | Out-Null
        return
    }
    Start-Process -FilePath "notepad.exe" -ArgumentList ('"' + $setupLog + '"') | Out-Null
})

$guideButton.Add_Click({
    if (Test-Path -LiteralPath $extensionGuide -PathType Leaf) {
        Start-Process -FilePath $extensionGuide | Out-Null
    } else {
        [System.Windows.Forms.MessageBox]::Show("extension\INSTALL.md が見つかりません。", "Local Voice Bridge", "OK", "Warning") | Out-Null
    }
})

$closeButton.Add_Click({ $form.Close() })
$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($null -ne $script:process -and -not $script:process.HasExited) {
        $eventArgs.Cancel = $true
        [System.Windows.Forms.MessageBox]::Show("セットアップ中は閉じられません。中止する場合は「キャンセル」を押してください。", "Local Voice Bridge", "OK", "Warning") | Out-Null
    }
})

Refresh-ProfileItems -PreferredProfile $InitialProfile
Update-ProfileDescription
[void]$form.ShowDialog()
$ErrorActionPreference = "Stop"
$LocalApiPort = 8717

$Root = Split-Path -Parent $PSScriptRoot
$LocalApiDir = Join-Path $Root "local-api"
$RuntimeDir = Join-Path $LocalApiDir "runtime"
$PidFile = Join-Path $RuntimeDir "local-api.pid"

$stopped = $false

function Get-PortOwnerProcessIds {
  param(
    [Parameter(Mandatory = $true)][int]$Port
  )

  $pids = @()
  try {
    $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $pids = @()
  }

  if (-not $pids -or $pids.Count -eq 0) {
    $netstatCmd = Get-Command netstat -ErrorAction SilentlyContinue
    if ($null -eq $netstatCmd) {
      $systemNetstat = Join-Path $env:WINDIR "System32\netstat.exe"
      if (Test-Path $systemNetstat) {
        $netstatCmd = [pscustomobject]@{ Source = $systemNetstat }
      }
    }

    if ($null -ne $netstatCmd) {
      try {
        $netstatExe = $netstatCmd.Source
        $netstat = & $netstatExe -ano -p tcp | Select-String -Pattern (":{0}\s+" -f $Port)
        $pids = @(
          $netstat |
          ForEach-Object {
            $line = ($_ -replace "^\s+", "") -replace "\s+", " "
            $parts = $line.Split(" ")
            if ($parts.Count -ge 5) {
              [int]$parts[4]
            }
          } |
          Where-Object { $_ -gt 0 } |
          Select-Object -Unique
        )
      } catch {
        $pids = @()
      }
    }
  }

  return @($pids | Where-Object { $_ -gt 0 -and $_ -ne $PID } | Select-Object -Unique)
}

if (Test-Path $PidFile) {
  $pidText = Get-Content -Raw $PidFile
  $procId = 0
  if ([int]::TryParse($pidText.Trim(), [ref]$procId) -and $procId -gt 0) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc) {
      Stop-Process -Id $procId -Force
      Write-Host "Stopped Local API by PID file: $procId"
      $stopped = $true
    }
  }
  Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
}

$portOwners = Get-PortOwnerProcessIds -Port $LocalApiPort
foreach ($ownerPid in $portOwners) {
  try {
    Stop-Process -Id $ownerPid -Force -ErrorAction Stop
    Write-Host "Stopped process on port ${LocalApiPort}: $ownerPid"
    $stopped = $true
  } catch {
    Write-Warning ("Failed to stop PID={0} on port {1}: {2}" -f $ownerPid, ${LocalApiPort}, $_.Exception.Message)
  }
}

$fallback = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like "*local-api*server.py*" -or $_.CommandLine -like "*17_chatgpt-local-voice-bridge*server.py*"
}

foreach ($proc in $fallback) {
  try {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
    Write-Host "Stopped Local API process: $($proc.ProcessId)"
    $stopped = $true
  } catch {}
}

if (-not $stopped) {
  Write-Host "No running Local API process was found."
}

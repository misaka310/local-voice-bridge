param(
  [int]$TimeoutSeconds = 30,
  [int]$PollMilliseconds = 400
)

$ErrorActionPreference = 'Stop'
$BaseUrl = 'http://127.0.0.1:8717'

function Invoke-LocalVoiceJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [ValidateSet('GET', 'POST')][string]$Method = 'GET',
    [hashtable]$Body
  )

  $parameters = @{
    Uri = "$BaseUrl$Path"
    Method = $Method
    TimeoutSec = 3
    UseBasicParsing = $true
  }
  if ($null -ne $Body) {
    $parameters.ContentType = 'application/json; charset=utf-8'
    $parameters.Body = $Body | ConvertTo-Json -Compress
  }
  $response = Invoke-RestMethod @parameters
  if ($null -eq $response -or $response.ok -ne $true) {
    throw "Local Voice Bridge request failed: $Path"
  }
  return $response
}

$initial = Invoke-LocalVoiceJson -Path '/v1/control-panel'
$extension = $initial.extension
if ($null -eq $extension -or $extension.connected -ne $true) {
  throw 'Local Voice Bridge extension is not connected. Safe self-reload is unavailable.'
}
if ($extension.supportsExtensionReload -ne $true) {
  throw 'The loaded extension does not support agent-operated reload.'
}
$sameVersionRefresh = $extension.updateRequired -ne $true

$baselineUpdatedAt = [double]($extension.updatedAt -as [double])
$expectedVersion = [string]$extension.expectedVersion
$command = Invoke-LocalVoiceJson -Path '/v1/control-panel/command' -Method POST -Body @{ command = 'reload_extension' }
$deadline = (Get-Date).AddSeconds([Math]::Max(5, $TimeoutSeconds))
$last = $null

do {
  Start-Sleep -Milliseconds ([Math]::Max(100, $PollMilliseconds))
  try {
    $last = Invoke-LocalVoiceJson -Path '/v1/control-panel'
  } catch {
    continue
  }
  $current = $last.extension
  if ($null -eq $current -or $current.connected -ne $true) {
    continue
  }
  $reconnected = [double]($current.updatedAt -as [double]) -gt $baselineUpdatedAt
  $versionMatches = [string]::IsNullOrWhiteSpace($expectedVersion) -or [string]$current.loadedVersion -eq $expectedVersion
  if ($reconnected -and $current.updateRequired -ne $true -and $versionMatches) {
    [ordered]@{
      ok = $true
      result = 'reloaded'
      sameVersionRefresh = [bool]$sameVersionRefresh
      commandId = $command.commandId
      loadedVersion = [string]$current.loadedVersion
      expectedVersion = $expectedVersion
      updatedAt = $current.updatedAt
    } | ConvertTo-Json -Depth 3
    exit 0
  }
} while ((Get-Date) -lt $deadline)

$evidence = if ($null -ne $last) { $last.extension } else { $null }
throw "Extension reload was requested but post-reload verification timed out. Evidence: $($evidence | ConvertTo-Json -Compress -Depth 4)"

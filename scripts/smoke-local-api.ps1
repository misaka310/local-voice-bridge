$ErrorActionPreference = "Stop"

$BaseUrl = $env:LOCAL_VOICE_PUBLIC_BASE_URL
if (-not $BaseUrl) {
  $BaseUrl = "http://127.0.0.1:8717"
}
$BaseUrl = $BaseUrl.TrimEnd("/")

Write-Host "Health: $BaseUrl/health"
$health = Invoke-RestMethod -Uri "$BaseUrl/health" -Method GET
if (-not $health.ok -or $health.engine -ne "irodori_direct") {
  throw "Expected healthy irodori_direct API"
}

Write-Host "Models: $BaseUrl/v1/models"
$models = Invoke-RestMethod -Uri "$BaseUrl/v1/models" -Method GET
if (-not $models.ok) {
  throw "Models check failed"
}
$modelIds = @($models.models | ForEach-Object { [string]$_.id })
if ($modelIds -notcontains "irodori-v3") {
  throw "Expected irodori-v3 in /v1/models"
}

Write-Host "Reference voices: $BaseUrl/v1/reference-voices"
$voices = Invoke-RestMethod -Uri "$BaseUrl/v1/reference-voices" -Method GET
if (-not $voices.ok) {
  throw "Reference voices check failed"
}

Write-Host "OK: irodori_direct / irodori-v3"

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [int]$Port = $(if ($env:SHORTS_PORT) { [int]$env:SHORTS_PORT } else { 8001 })
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

& ".\.venv\Scripts\shorts-health.exe"

try {
    $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health/ready" -TimeoutSec 10
    $ready | ConvertTo-Json -Depth 5
} catch {
    Write-Warning "Pipeline HTTP readiness endpoint is not reachable: $($_.Exception.Message)"
}

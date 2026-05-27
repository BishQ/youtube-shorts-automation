# Quick test: LM Studio connected + writes a short script line.
# Usage: .\scripts\windows\test_lmstudio.ps1

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$BaseUrl = "http://127.0.0.1:1234/v1",
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load_dotenv.ps1") -EnvFile (Join-Path $ProjectRoot ".env")

if (-not $Model -and $env:SHORTS_LOCAL_LLM_MODEL) {
    $Model = $env:SHORTS_LOCAL_LLM_MODEL
}
if (-not $Model) {
    $Model = "google/gemma-4-e4b"
}

Write-Host "[test] LM Studio at $BaseUrl" -ForegroundColor Cyan

try {
    $models = Invoke-RestMethod -Uri "$BaseUrl/models" -TimeoutSec 5
} catch {
    Write-Host "[test] FAIL: Local Server not running." -ForegroundColor Red
    Write-Host "  In LM Studio: Developer tab -> Local Server -> Start (port 1234)" -ForegroundColor Yellow
    Write-Host "  Load model: Gemma 4 E4B Instruct (your screenshot model)" -ForegroundColor Yellow
    exit 1
}

Write-Host "[test] Server OK. Models:" -ForegroundColor Green
if ($models.data) {
    foreach ($m in $models.data) {
        Write-Host "  - $($m.id)"
    }
} else {
    $models | ConvertTo-Json -Depth 4
}

$body = @{
    model = $Model
    messages = @(
        @{
            role = "user"
            content = "Write exactly 3 short sentences about Genghis Khan for a YouTube Short. English only."
        }
    )
    max_tokens = 200
    temperature = 0.4
} | ConvertTo-Json -Depth 5

Write-Host "[test] Asking model '$Model' for a mini script..." -ForegroundColor Cyan

try {
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/chat/completions" `
        -ContentType "application/json" -Body $body -TimeoutSec 120
    $text = $resp.choices[0].message.content
    Write-Host "[test] SUCCESS - model replied:" -ForegroundColor Green
    Write-Host $text
    Write-Host ""
    Write-Host "[test] Pipeline .env model name is OK. Use Web UI to run full plan." -ForegroundColor Green
} catch {
    Write-Host "[test] Chat failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Fix: In LM Studio load Gemma 4, then check model id above and set in .env:" -ForegroundColor Yellow
    Write-Host "  SHORTS_LOCAL_LLM_MODEL=<exact id from list>" -ForegroundColor Yellow
    exit 1
}

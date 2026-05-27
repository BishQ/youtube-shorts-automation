# Check LM Studio local server and sync .env (Windows).
#
# LM Studio must be started manually:
#   1. Open LM Studio
#   2. Load a model (e.g. Qwen2.5 7B Q4)
#   3. Developer / Local Server -> Start Server (port 1234)
#
# Usage:
#   .\scripts\windows\start_lmstudio.ps1
#   .\scripts\windows\start_lmstudio.ps1 -OpenApp

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [int]$Port = $(if ($env:LMSTUDIO_PORT) { [int]$env:LMSTUDIO_PORT } else { 1234 }),
    [string]$Model = $(if ($env:SHORTS_LOCAL_LLM_MODEL) { $env:SHORTS_LOCAL_LLM_MODEL } else { "" }),
    [switch]$OpenApp,
    [switch]$SkipEnvSync
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

function Write-LmsLog([string]$Message, [string]$Color = "Cyan") {
    Write-Host "[lmstudio] $Message" -ForegroundColor $Color
}

function Test-LmStudioReady([int]$ListenPort) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:${ListenPort}/v1/models" -TimeoutSec 3
        return $r
    } catch {
        return $null
    }
}

function Try-OpenLmStudio {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\LM Studio\LM Studio.exe",
        "$env:ProgramFiles\LM Studio\LM Studio.exe"
    )
    foreach ($exe in $candidates) {
        if (Test-Path $exe) {
            Write-LmsLog "Opening LM Studio..."
            Start-Process $exe
            return $true
        }
    }
    return $false
}

$models = Test-LmStudioReady -ListenPort $Port
if ($models) {
    Write-LmsLog "Server OK on http://127.0.0.1:${Port}/v1" "Green"
    $ids = @()
    if ($models.data) {
        $ids = $models.data | ForEach-Object { $_.id }
    } elseif ($models -is [array]) {
        $ids = $models | ForEach-Object { $_.id }
    }
    if ($ids.Count -gt 0) {
        Write-LmsLog "Loaded model id(s): $($ids -join ', ')"
        if (-not $Model) {
            $Model = $ids[0]
            Write-LmsLog "Using model id for .env: $Model"
        }
    }
} else {
    Write-LmsLog "Server not running on port $Port." "Yellow"
    if ($OpenApp) {
        if (-not (Try-OpenLmStudio)) {
            Write-LmsLog "Install LM Studio from https://lmstudio.ai then start Local Server." "Yellow"
        }
    } else {
        Write-LmsLog "Run: .\scripts\windows\start_lmstudio.ps1 -OpenApp" "Yellow"
    }
    Write-LmsLog "In LM Studio: load model -> Developer -> Local Server -> Start (port $Port)" "Yellow"
    exit 1
}

if (-not $SkipEnvSync) {
    $sync = Join-Path $ProjectRoot "scripts\windows\sync_env_lmstudio.ps1"
    if (Test-Path $sync) {
        & $sync -EnvFile (Join-Path $ProjectRoot ".env") -Port $Port -Model $(if ($Model) { $Model } else { "local-model" })
        if ($LASTEXITCODE -ne 0) {
            Write-LmsLog ".env sync failed (file locked?). Set SHORTS_LOCAL_LLM_MODEL=$Model manually." "Yellow"
        }
    }
}

Write-LmsLog "Pipeline expects: SHORTS_LOCAL_LLM_STRUCTURED_OUTPUT=json_schema (LM Studio)"
exit 0

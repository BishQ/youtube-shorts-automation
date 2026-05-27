# Upsert .env for LM Studio (Windows local LLM).
#
# Usage:
#   .\scripts\windows\sync_env_lmstudio.ps1
#   .\scripts\windows\sync_env_lmstudio.ps1 -Model "qwen2.5-7b-instruct"

param(
    [string]$EnvFile = (Join-Path (Resolve-Path "$PSScriptRoot\..\..").Path ".env"),
    [int]$Port = $(if ($env:LMSTUDIO_PORT) { [int]$env:LMSTUDIO_PORT } else { 1234 }),
    [string]$Model = $(if ($env:SHORTS_LOCAL_LLM_MODEL) { $env:SHORTS_LOCAL_LLM_MODEL } else { "local-model" })
)

$ErrorActionPreference = "Stop"

$Keys = [ordered]@{
    SHORTS_PLANNER_BACKEND              = "vllm"
    SHORTS_LOCAL_LLM_BASE_URL         = "http://127.0.0.1:${Port}/v1"
    SHORTS_LOCAL_LLM_MODEL            = $Model
    SHORTS_LOCAL_LLM_TIMEOUT_S        = "600"
    SHORTS_LOCAL_LLM_TEMPERATURE      = "0.4"
    SHORTS_LOCAL_LLM_PLANNER_TEMPERATURE = "0.05"
    SHORTS_LOCAL_LLM_STRUCTURED_OUTPUT = "json_schema"
    SHORTS_LOCAL_LLM_MAX_TOKENS       = "4000"
}

function Write-EnvFileAtomic([string]$File, [string[]]$Lines) {
    $dir = Split-Path -Parent $File
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $tmp = "$File.sync_tmp"
    $text = ($Lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText($tmp, $text, [System.Text.UTF8Encoding]::new($false))
    if (Test-Path $File) {
        Remove-Item -Force $File -ErrorAction Stop
    }
    Move-Item -Force $tmp $File
}

Write-Host "[lmstudio] Updating $EnvFile"

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

$content = @(Get-Content $EnvFile -ErrorAction SilentlyContinue)
$out = New-Object System.Collections.Generic.List[string]
$seen = @{}

foreach ($raw in $content) {
    $matched = $false
    foreach ($key in $Keys.Keys) {
        $pattern = "^\s*$([regex]::Escape($key))\s*="
        if ($raw -match $pattern) {
            if (-not $seen[$key]) {
                $out.Add("${key}=$($Keys[$key])")
                $seen[$key] = $true
            }
            $matched = $true
            break
        }
    }
    if (-not $matched) {
        $out.Add($raw)
    }
}

foreach ($key in $Keys.Keys) {
    if (-not $seen[$key]) {
        $out.Add("${key}=$($Keys[$key])")
    }
}

$maxAttempts = 8
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    try {
        Write-EnvFileAtomic -File $EnvFile -Lines $out.ToArray()
        Write-Host "[lmstudio] Done."
        exit 0
    } catch {
        if ($attempt -eq $maxAttempts) {
            Write-Warning "[lmstudio] Could not write ${EnvFile} (locked?). Close .env in the editor and re-run."
            exit 1
        }
        Start-Sleep -Milliseconds 400
    }
}

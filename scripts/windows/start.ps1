param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$ComfyRoot = "",
    [int]$Port = 0,
    [switch]$SkipComfy,
    [switch]$SkipLlm,
    [switch]$SkipVllm
)

$ErrorActionPreference = "Stop"
# Strip any embedded quotes from $ProjectRoot (caller may pass them) and use -LiteralPath
$ProjectRoot = $ProjectRoot.Trim().Trim('"').Trim("'")
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $PSScriptRoot "load_dotenv.ps1") -EnvFile (Join-Path $ProjectRoot ".env")

if (-not $ComfyRoot -and $env:SHORTS_COMFYUI_ROOT) { $ComfyRoot = $env:SHORTS_COMFYUI_ROOT }
if ($Port -le 0) {
    if ($env:SHORTS_PORT) { $Port = [int]$env:SHORTS_PORT } else { $Port = 8001 }
}

function Write-StartLog([string]$Message, [string]$Color = "White") {
    Write-Host "[start] $Message" -ForegroundColor $Color
}

function Test-HttpOk([string]$Url, [int]$TimeoutSec = 3) {
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-ForUrl([string]$Url, [string]$Label, [int]$MaxSeconds = 120) {
    for ($i = 1; $i -le $MaxSeconds; $i++) {
        if (Test-HttpOk $Url) {
            Write-StartLog "$Label ready." "Green"
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-StartLog "$Label not ready at $Url" "Yellow"
    return $false
}

Write-StartLog "Project: $ProjectRoot" "Cyan"
Write-StartLog "Web UI: http://127.0.0.1:$Port" "Cyan"
Write-StartLog "GPU serial: plan (LM Studio) -> unload LLM -> images/i2v (ComfyUI)" "Cyan"

$skipLlm = $SkipLlm -or $SkipVllm

# ── 1. LM Studio (planner) ───────────────────────────────────────────────────
if (-not $skipLlm) {
    $startLm = Join-Path $ProjectRoot "scripts\windows\start_lmstudio.ps1"
    if (Test-Path $startLm) {
        Write-StartLog "LM Studio (script writer)..."
        & $startLm -ProjectRoot $ProjectRoot -OpenApp
        if ($LASTEXITCODE -ne 0) {
            Write-StartLog "LM Studio: open app -> load google/gemma-4-e4b -> Developer -> Local Server -> Start (port 1234)" "Yellow"
        } else {
            Wait-ForUrl "http://127.0.0.1:1234/v1/models" "LM Studio" 45 | Out-Null
        }
    }
}

# ── 2. ComfyUI (images + Wan video) ──────────────────────────────────────────
if (-not $SkipComfy) {
    if (-not $ComfyRoot) {
        Write-StartLog "Set SHORTS_COMFYUI_ROOT in .env (ComfyUI folder path)." "Yellow"
    } elseif (Test-Path $ComfyRoot) {
        if (-not (Test-HttpOk "http://127.0.0.1:8188/system_stats")) {
            Write-StartLog "Starting ComfyUI..."
            $comfyCmd = "Set-Location '$ComfyRoot'; .\python_embeded\python.exe -s .\ComfyUI\main.py --windows-standalone-build --port 8188 --lowvram"
            Write-StartLog "ComfyUI flags: --lowvram (required for 8GB + Qwen GGUF)" "Cyan"
            Start-Process powershell -ArgumentList "-NoExit", "-Command", $comfyCmd -WindowStyle Normal
            Wait-ForUrl "http://127.0.0.1:8188/system_stats" "ComfyUI" 180 | Out-Null
        } else {
            Write-StartLog "ComfyUI already running on :8188" "Green"
        }
    } else {
        throw "ComfyUI root not found: $ComfyRoot"
    }
}

# ── 3. Pipeline Web UI ───────────────────────────────────────────────────────
if (-not (Test-HttpOk "http://127.0.0.1:$Port/api/health" 2)) {
    $serverExe = Join-Path $ProjectRoot ".venv\Scripts\shorts-server.exe"
    if (-not (Test-Path $serverExe)) {
        throw "Run scripts\windows\install.ps1 first (missing shorts-server.exe)."
    }
    Write-StartLog "Starting pipeline Web UI..."
    $serverCmd = "Set-Location '$ProjectRoot'; & '$serverExe' --host 127.0.0.1 --port $Port"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $serverCmd -WindowStyle Normal
    Wait-ForUrl "http://127.0.0.1:$Port/api/health" "Web UI" 60 | Out-Null
} else {
    Write-StartLog "Web UI already running on :$Port" "Green"
}

Start-Process "http://127.0.0.1:$Port"

Write-StartLog "Done. Open Web UI -> create/run job. Plan runs first; LM unloads before images." "Green"

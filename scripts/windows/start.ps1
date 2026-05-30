param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$ComfyRoot = "",
    [int]$Port = 0,
    [switch]$SkipComfy,
    [switch]$SkipLlm,
    [switch]$SkipVllm
)

$ErrorActionPreference = "Stop"
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

function Test-UrlIsLocal([string]$BaseUrl) {
    if (-not $BaseUrl) { return $true }
    try {
        $hostName = ([Uri]$BaseUrl.Trim().TrimEnd('/')).Host.ToLowerInvariant()
        return $hostName -in @('127.0.0.1', 'localhost', '::1')
    } catch {
        return $true
    }
}

function Get-ComfyBaseUrl {
    if ($env:SHORTS_COMFY_BASE_URL) {
        return $env:SHORTS_COMFY_BASE_URL.Trim().TrimEnd('/')
    }
    return "http://127.0.0.1:8188"
}

function Get-LlmModelsUrl {
    $base = ($env:SHORTS_LOCAL_LLM_BASE_URL -or "http://127.0.0.1:11434/v1").Trim().TrimEnd('/')
    if ($base -match '/v1$') {
        return "$base/models"
    }
    return "$base/v1/models"
}

$comfyBase = Get-ComfyBaseUrl
$comfyStatsUrl = "$comfyBase/system_stats"
$comfyIsLocal = Test-UrlIsLocal $comfyBase
$i2vBackend = ($env:SHORTS_I2V_BACKEND -or "ltx").Trim().ToLowerInvariant()
$i2vEnabled = ($env:SHORTS_I2V_ENABLED -or "true").Trim().ToLowerInvariant() -ne "false"
$imageBackend = ($env:SHORTS_IMAGE_BACKEND -or "comfy").Trim().ToLowerInvariant()
$llmModelsUrl = Get-LlmModelsUrl
$useOllama = $llmModelsUrl -match ':11434/'

Write-StartLog "Project: $ProjectRoot" "Cyan"
Write-StartLog "Web UI: http://127.0.0.1:$Port" "Cyan"
Write-StartLog "Image backend: $imageBackend | I2V: $(if ($i2vEnabled) { $i2vBackend } else { 'off' })" "Cyan"
Write-StartLog "ComfyUI: $comfyBase $(if ($comfyIsLocal) { '(local)' } else { '(remote RunPod)' })" "Cyan"

$skipLlm = $SkipLlm -or $SkipVllm

# ── 1. Planner LLM (Ollama or LM Studio) ─────────────────────────────────────
if (-not $skipLlm) {
    if ($useOllama) {
        Write-StartLog "Planner: Ollama ($llmModelsUrl)..."
        if (-not (Wait-ForUrl $llmModelsUrl "Ollama" 30)) {
            Write-StartLog "Start Ollama and load model '$($env:SHORTS_LOCAL_LLM_MODEL)' before running jobs." "Yellow"
        }
    } else {
        $startLm = Join-Path $ProjectRoot "scripts\windows\start_lmstudio.ps1"
        if (Test-Path $startLm) {
            Write-StartLog "Planner: LM Studio..."
            & $startLm -ProjectRoot $ProjectRoot -OpenApp
            if ($LASTEXITCODE -ne 0) {
                Write-StartLog "LM Studio: load model -> Developer -> Local Server -> Start" "Yellow"
            } else {
                Wait-ForUrl $llmModelsUrl "LM Studio" 45 | Out-Null
            }
        }
    }
}

# ── 2. ComfyUI (local Windows portable OR remote RunPod proxy) ────────────────
if (-not $SkipComfy) {
    if (-not $comfyIsLocal) {
        Write-StartLog "Remote ComfyUI — not starting local copy." "Cyan"
        if (Test-HttpOk $comfyStatsUrl 15) {
            Write-StartLog "Remote ComfyUI ready." "Green"
        } else {
            Write-StartLog "Remote ComfyUI DOWN — fix RunPod pod or SHORTS_COMFY_BASE_URL in .env" "Yellow"
            Write-StartLog "Expected: https://<pod-id>-8188.proxy.runpod.net" "Yellow"
        }
    } elseif (-not $ComfyRoot) {
        Write-StartLog "Set SHORTS_COMFYUI_ROOT in .env for local ComfyUI." "Yellow"
    } elseif (Test-Path $ComfyRoot) {
        if (-not (Test-HttpOk $comfyStatsUrl)) {
            Write-StartLog "Starting local ComfyUI..."
            $comfyCmd = "Set-Location '$ComfyRoot'; .\python_embeded\python.exe -s .\ComfyUI\main.py --windows-standalone-build --port 8188 --lowvram"
            Write-StartLog "ComfyUI flags: --lowvram" "Cyan"
            Start-Process powershell -ArgumentList "-NoExit", "-Command", $comfyCmd -WindowStyle Normal
            Wait-ForUrl $comfyStatsUrl "ComfyUI" 180 | Out-Null
        } else {
            Write-StartLog "ComfyUI already running at $comfyBase" "Green"
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

Write-StartLog "Done. Web UI -> create/run job. GPU work uses ComfyUI at $comfyBase" "Green"

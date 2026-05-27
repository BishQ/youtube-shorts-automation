# Start vLLM OpenAI API server (Linux/WSL or Windows with a working vllm._C build).
# On Windows use LM Studio instead: scripts/windows/start_lmstudio.ps1
#
# Usage (from repo root):
#   .\scripts\windows\start_vllm.ps1
#
# Env overrides (optional):
#   $env:VLLM_MODEL = "Qwen/Qwen3.5-9B-Instruct-GPTQ-Int4"
#   $env:VLLM_QUANTIZATION = "gptq"
#   $env:VLLM_SERVED_NAME = "qwen3.5-9b-gptq"
#   $env:VLLM_PORT = "8000"
#   $env:VLLM_MAX_MODEL_LEN = "16384"
#   $env:VLLM_GPU_MEMORY_UTILIZATION = "0.92"
#   $env:VLLM_PYTHON = "C:\path\to\python.exe"   # CUDA-enabled Python with vLLM

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$Model = $(if ($env:VLLM_MODEL) { $env:VLLM_MODEL } else { "Qwen/Qwen3.5-9B-Instruct-GPTQ-Int4" }),
    [string]$Quantization = $(if ($env:VLLM_QUANTIZATION) { $env:VLLM_QUANTIZATION } else { "gptq" }),
    [string]$ServedName = $(if ($env:VLLM_SERVED_NAME) { $env:VLLM_SERVED_NAME } else { "qwen3.5-9b-gptq" }),
    [int]$Port = $(if ($env:VLLM_PORT) { [int]$env:VLLM_PORT } else { 8000 }),
    [int]$MaxModelLen = $(if ($env:VLLM_MAX_MODEL_LEN) { [int]$env:VLLM_MAX_MODEL_LEN } else { 16384 }),
    [double]$GpuMemoryUtilization = $(if ($env:VLLM_GPU_MEMORY_UTILIZATION) { [double]$env:VLLM_GPU_MEMORY_UTILIZATION } else { 0.92 }),
    [string]$ListenHost = $(if ($env:VLLM_HOST) { $env:VLLM_HOST } else { "127.0.0.1" }),
    [switch]$SkipInstall,
    [switch]$SkipEnvSync,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "vllm.log"
$ErrLogFile = Join-Path $LogDir "vllm.err.log"

function Write-VllmLog([string]$Message, [string]$Color = "Cyan") {
    Write-Host "[vllm] $Message" -ForegroundColor $Color
}

function Get-VllmPython {
    if ($env:VLLM_PYTHON -and (Test-Path $env:VLLM_PYTHON)) {
        return (Resolve-Path $env:VLLM_PYTHON).Path
    }
    if (Test-Path ".\.venv\Scripts\python.exe") {
        return (Resolve-Path ".\.venv\Scripts\python.exe").Path
    }
    return "python"
}

function Show-VllmLogTail([int]$Lines = 40) {
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail $Lines -ErrorAction SilentlyContinue
    }
    if (Test-Path $ErrLogFile) {
        Get-Content $ErrLogFile -Tail $Lines -ErrorAction SilentlyContinue
    }
}

function Format-ProcessArg([string]$Value) {
    if ($Value -match '[\s"]') {
        '"' + ($Value -replace '"', '\"') + '"'
    } else {
        $Value
    }
}

function Test-VllmReady([int]$ListenPort) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:${ListenPort}/v1/models" -TimeoutSec 3
        return $null -ne $r
    } catch {
        return $false
    }
}

function Stop-ExistingVllm {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*vllm.entrypoints.openai.api_server*"
        } |
        ForEach-Object {
            Write-VllmLog "Stopping existing vLLM (PID $($_.ProcessId))..." "Yellow"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 2
}

if (Test-VllmReady -ListenPort $Port) {
    Write-VllmLog "Already running on port $Port."
    Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/v1/models" | ConvertTo-Json -Depth 6
    exit 0
}

$python = Get-VllmPython
Write-VllmLog "Python: $python"

$vllmCCheck = & $python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('vllm._C') else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-VllmLog "vLLM CUDA module (vllm._C) is missing — Windows pip install cannot run models on GPU." "Red"
    Write-VllmLog "Fix: use WSL2 (recommended) or a pre-built Windows vLLM wheel (see repo docs / community vllm-windows-build)." "Yellow"
    Write-VllmLog "WSL quick start: wsl --install  ->  Ubuntu  ->  pip install vllm  ->  run this server in Linux." "Yellow"
    exit 1
}
Write-VllmLog "Model: $Model"
Write-VllmLog "Quantization: $Quantization"
Write-VllmLog "Served name: $ServedName  Port: $Port  max_model_len: $MaxModelLen  gpu_mem: $GpuMemoryUtilization"

if (-not $SkipInstall) {
    Write-VllmLog "Installing/upgrading vLLM (first run can take several minutes)..."
    & $python -m pip install -q -U vllm
}

if (-not $SkipEnvSync) {
    $syncScript = Join-Path $ProjectRoot "scripts\windows\sync_env_llm.ps1"
    if (Test-Path $syncScript) {
        & $syncScript -EnvFile (Join-Path $ProjectRoot ".env") -Port $Port -ServedName $ServedName
        if ($LASTEXITCODE -ne 0) {
            Write-VllmLog ".env sync skipped (file locked). vLLM will still start." "Yellow"
            Write-VllmLog "Ensure .env has SHORTS_LOCAL_LLM_MODEL=$ServedName and SHORTS_LOCAL_LLM_BASE_URL=http://127.0.0.1:${Port}/v1" "Yellow"
        }
    }
}

Stop-ExistingVllm

$hfHome = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:USERPROFILE ".cache\huggingface" }
$env:HF_HOME = $hfHome

$launcher = (Resolve-Path (Join-Path $ProjectRoot "scripts\windows\run_vllm_server.py")).Path

$vllmArgList = @(
    $launcher,
    "--model", $Model,
    "--quantization", $Quantization,
    "--served-model-name", $ServedName,
    "--port", "$Port",
    "--host", $ListenHost,
    "--gpu-memory-utilization", "$GpuMemoryUtilization",
    "--max-model-len", "$MaxModelLen",
    "--trust-remote-code"
)
# Start-Process splits unquoted paths at spaces on Windows (e.g. "Youtube shorts automation").
$vllmArgLine = ($vllmArgList | ForEach-Object { Format-ProcessArg $_ }) -join ' '

Write-VllmLog "Starting server (log: $LogFile, errors: $ErrLogFile)..."
Write-VllmLog "Command: $python $vllmArgLine"

# PowerShell cannot redirect stdout and stderr to the same file via Start-Process.
Remove-Item $LogFile, $ErrLogFile -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $python `
    -ArgumentList $vllmArgLine `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrLogFile `
    -WindowStyle Normal `
    -PassThru

Write-VllmLog "PID $($proc.Id) - loading weights (9B GPTQ: ~1-3 min on first run)."

if ($NoWait) {
    Write-VllmLog "NoWait set; check http://127.0.0.1:${Port}/v1/models when ready."
    exit 0
}

for ($i = 1; $i -le 120; $i++) {
    if (Test-VllmReady -ListenPort $Port) {
        Write-VllmLog "Ready at http://127.0.0.1:${Port}/v1" "Green"
        Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/v1/models" | ConvertTo-Json -Depth 6
        exit 0
    }
    if ($proc.HasExited) {
        Write-VllmLog "vLLM exited early - last log lines:" "Red"
        Show-VllmLogTail -Lines 40
        exit 1
    }
    if ($i % 6 -eq 0) {
        Write-VllmLog "Still loading... ($($i * 5) seconds)"
        Show-VllmLogTail -Lines 3
    }
    Start-Sleep -Seconds 5
}

Write-VllmLog "Not ready after 600s - see ${LogFile} and ${ErrLogFile}" "Red"
Show-VllmLogTail -Lines 40
exit 1

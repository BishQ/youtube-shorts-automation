param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$ComfyRoot = $env:SHORTS_COMFYUI_ROOT,
    [int]$Port = $(if ($env:SHORTS_PORT) { [int]$env:SHORTS_PORT } else { 8001 }),
    [switch]$SkipComfy,
    [switch]$SkipVllm
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

if (-not $SkipVllm) {
    $llmUrl = $env:SHORTS_LOCAL_LLM_BASE_URL
    if (-not $llmUrl) { $llmUrl = "http://127.0.0.1:8000/v1" }
    Write-Host "vLLM is expected on $llmUrl."
    Write-Host "Start vLLM separately with your model command before production batches."
}

if (-not $SkipComfy) {
    if (-not $ComfyRoot) {
        Write-Host "SHORTS_COMFYUI_ROOT is not set; skipping ComfyUI launch."
    } elseif (Test-Path $ComfyRoot) {
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ComfyRoot'; .\python_embeded\python.exe -s .\ComfyUI\main.py --windows-standalone-build --port 8188" -WindowStyle Normal
    } else {
        throw "ComfyUI root not found: $ComfyRoot"
    }
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ProjectRoot'; .\.venv\Scripts\shorts-server.exe --host 127.0.0.1 --port $Port" -WindowStyle Normal
Start-Process "http://127.0.0.1:$Port"

Write-Host "Pipeline server requested at http://127.0.0.1:$Port"

# Restart ComfyUI with --lowvram (fixes 0% stuck on 8GB GPUs with Qwen GGUF).
param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load_dotenv.ps1") -EnvFile (Join-Path $ProjectRoot ".env")

$comfyRoot = $env:SHORTS_COMFYUI_ROOT
if (-not $comfyRoot -or -not (Test-Path $comfyRoot)) {
    throw "Set SHORTS_COMFYUI_ROOT in .env"
}

Write-Host "[comfy] Interrupt + restart with --lowvram..." -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8188/interrupt" -Method Post -TimeoutSec 5 | Out-Null
} catch { }

Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "*ComfyUI_windows_portable*"
} | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 3

$cmd = "Set-Location '$comfyRoot'; .\python_embeded\python.exe -s .\ComfyUI\main.py --windows-standalone-build --port 8188 --lowvram"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WindowStyle Normal

for ($i = 1; $i -le 120; $i++) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 3 | Out-Null
        Write-Host "[comfy] ready on :8188 (lowvram)" -ForegroundColor Green
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    }
}
Write-Host "[comfy] not ready after 120s - check Comfy window" -ForegroundColor Yellow
exit 1

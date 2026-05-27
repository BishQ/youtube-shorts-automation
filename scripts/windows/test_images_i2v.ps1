# Test Comfy images + Wan I2V using existing plan (script).
# Usage:
#   .\scripts\windows\test_images_i2v.ps1 -JobId genghis-khan-0f983561 -PromoteFailed -DevSkipPlanValidation
#   .\scripts\windows\test_images_i2v.ps1 -JobId plato-48bd1827 -I2vOnly

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [Parameter(Mandatory = $true)]
    [string]$JobId,
    [switch]$PromoteFailed,
    [switch]$DevSkipPlanValidation,
    [switch]$ImagesOnly,
    [switch]$I2vOnly,
    [switch]$SkipComfyStart
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot
. (Join-Path $PSScriptRoot "load_dotenv.ps1") -EnvFile (Join-Path $ProjectRoot ".env")

if (-not $SkipComfyStart) {
    $comfyRoot = $env:SHORTS_COMFYUI_ROOT
    if (-not $comfyRoot -or -not (Test-Path $comfyRoot)) {
        throw "Set SHORTS_COMFYUI_ROOT in .env"
    }
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 3 | Out-Null
        Write-Host "[comfy] already running :8188" -ForegroundColor Green
    } catch {
        Write-Host "[comfy] starting ComfyUI (new window)..." -ForegroundColor Cyan
        $cmd = "Set-Location '$comfyRoot'; .\python_embeded\python.exe -s .\ComfyUI\main.py --windows-standalone-build --port 8188 --lowvram"
        Write-Host "[comfy] --lowvram enabled (8GB + Qwen)" -ForegroundColor Cyan
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd -WindowStyle Normal
        for ($i = 1; $i -le 180; $i++) {
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSec 3 | Out-Null
                Write-Host "[comfy] ready" -ForegroundColor Green
                break
            } catch {
                Start-Sleep -Seconds 1
            }
        }
    }
}

$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$args = @("scripts\test_images_i2v.py", "--job-id", $JobId)
if ($PromoteFailed) { $args += "--promote-failed" }
if ($DevSkipPlanValidation) { $args += "--dev-skip-plan-validation" }
if ($ImagesOnly) { $args += "--images" }
elseif ($I2vOnly) { $args += "--i2v" }

Write-Host "[pipeline] $($args -join ' ')" -ForegroundColor Cyan
& $py @args
exit $LASTEXITCODE

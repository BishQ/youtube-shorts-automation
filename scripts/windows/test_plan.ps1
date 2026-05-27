# Full script/plan test via LM Studio (plan stage only).
# Usage: .\scripts\windows\test_plan.ps1
#        .\scripts\windows\test_plan.ps1 -Figure "Cleopatra"

param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$Figure = "Genghis Khan"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

. (Join-Path $PSScriptRoot "load_dotenv.ps1") -EnvFile (Join-Path $ProjectRoot ".env")

Write-Host "[plan-test] LM Studio must be Running on :1234 with gemma-4-e4b loaded" -ForegroundColor Cyan

$py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "Run scripts\windows\install.ps1 first."
}

& $py (Join-Path $ProjectRoot "scripts\test_plan_only.py") --figure $Figure
exit $LASTEXITCODE

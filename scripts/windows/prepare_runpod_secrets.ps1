# Export Telegram secrets from local .env for RunPod merge.
# Usage:
#   .\scripts\windows\prepare_runpod_secrets.ps1
# Upload .env.runpod.secrets to the pod, then:
#   bash scripts/setup_runpod_env.sh --merge-secrets /workspace/youtube-shorts-automation/.env.runpod.secrets

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$RepoRoot\.env")) {
    Write-Error ".env not found at $RepoRoot\.env"
}

$keys = @(
    "SHORTS_TG_API_ID",
    "SHORTS_TG_API_HASH",
    "SHORTS_TG_TARGET_CHAT",
    "SHORTS_TG_INVITE_LINK"
)

$out = Join-Path $RepoRoot ".env.runpod.secrets"
$lines = @(
    "# Upload this file to RunPod; do not commit.",
    "# Generated: $(Get-Date -Format o)"
)

foreach ($key in $keys) {
    $match = Select-String -Path "$RepoRoot\.env" -Pattern "^$key=" -SimpleMatch:$false | Select-Object -First 1
    if ($match) {
        $lines += $match.Line
    }
}

$lines | Set-Content -Path $out -Encoding utf8
Write-Host "Wrote $out"
Write-Host "Upload to pod and run:"
Write-Host "  bash scripts/setup_runpod_env.sh --merge-secrets /workspace/youtube-shorts-automation/.env.runpod.secrets"

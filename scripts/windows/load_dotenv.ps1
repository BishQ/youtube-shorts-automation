# Read KEY=VALUE lines from repo .env into the current PowerShell session.
param(
    [string]$EnvFile = (Join-Path (Split-Path $PSScriptRoot -Parent | Split-Path -Parent) ".env")
)

if (-not (Test-Path $EnvFile)) {
    return
}

Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim()
    if ($val.StartsWith('"') -and $val.EndsWith('"')) {
        $val = $val.Substring(1, $val.Length - 2)
    }
    Set-Item -Path "env:$key" -Value $val
}

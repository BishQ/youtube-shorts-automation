$ErrorActionPreference = "Stop"

Get-Process -Name "uvicorn", "python" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like "*Shorts Pipeline*" } |
    Stop-Process -Force

Write-Host "Requested Shorts Pipeline stop. Stop ComfyUI/vLLM windows separately if they were launched manually."

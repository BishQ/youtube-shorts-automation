@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  Start pipeline WITHOUT WAN i2v (faster, static images only)
REM  Plan → Images (ComfyUI) → TTS → Whisper align → Final video (ken-burns)
REM ─────────────────────────────────────────────────────────────────────────
setlocal
set "SHORTS_I2V_ENABLED=false"
set "PIPELINE_MODE=no_wan"

cd /d "%~dp0"
echo.
echo ============================================================
echo  PIPELINE MODE: NO WAN (static images, ken-burns animation)
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\start.ps1"
endlocal
pause

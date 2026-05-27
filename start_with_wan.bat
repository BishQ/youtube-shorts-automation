@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  Start pipeline WITH WAN image-to-video animation
REM  Plan → Images (ComfyUI) → TTS → Whisper align → WAN i2v → Final video
REM ─────────────────────────────────────────────────────────────────────────
setlocal
set "SHORTS_I2V_ENABLED=true"
set "PIPELINE_MODE=with_wan"

cd /d "%~dp0"
echo.
echo ============================================================
echo  PIPELINE MODE: WITH WAN i2v (full cinematic animation)
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows\start.ps1"
endlocal
pause

@echo off
title YouTube Shorts - START
cd /d "%~dp0"

echo.
echo  Starting:
echo    [1] Ollama or LM Studio  (plan / script)
echo    [2] ComfyUI local OR RunPod remote  (images + LTX video)
echo    [3] Web UI     (control panel)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start.ps1" -ProjectRoot "%~dp0"
set ERR=%ERRORLEVEL%

echo.
if %ERR% NEQ 0 (
  echo  Something failed. Read the red/yellow messages above.
  pause
  exit /b %ERR%
)

echo  All services started. Browser should open http://127.0.0.1:8001
echo  If yellow warnings: Ollama model loaded OR RunPod ComfyUI URL in .env
echo.
timeout /t 5 >nul

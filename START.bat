@echo off
title YouTube Shorts - START
cd /d "%~dp0"

echo.
echo  Starting:
echo    [1] LM Studio  (script / plan)
echo    [2] ComfyUI    (images + video)
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
echo  LM Studio: load model + Start Local Server if the yellow warning appeared.
echo.
timeout /t 5 >nul

@echo off
title YouTube Shorts Automation — Launcher
color 0A
cls

echo ============================================================
echo   YouTube Shorts Automation  ^|  One-click launcher
echo ============================================================
echo.

:: ── Paths (edit these if you move anything) ─────────────────────────────────
set "PROJECT=C:\Users\35383\Documents\Youtube shorts automation"
set "COMFYUI=C:\Users\35383\Downloads\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable"
set "VENV=%PROJECT%\.venv\Scripts"
:: Pipeline HTTP port. Use 8001 if another app (e.g. TradeVault) already uses 8000.
set "SHORTS_PORT=8001"

:: ── 1. Start ComfyUI in its own window ─────────────────────────────────────
echo [1/2]  Starting ComfyUI...
tasklist /FI "WINDOWTITLE eq ComfyUI*" 2>NUL | find /I "cmd.exe" >NUL
start "ComfyUI" /D "%COMFYUI%" cmd /k "python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --port 8188"
timeout /t 5 /nobreak >NUL
echo        ComfyUI window opened  (http://127.0.0.1:8188)

:: ── 2. Start pipeline server in its own window ─────────────────────────────
echo [2/2]  Starting Pipeline server...
start "Shorts Pipeline" /D "%PROJECT%" cmd /k ""%VENV%\uvicorn.exe" shorts_pipeline.web.app:app --host 127.0.0.1 --port %SHORTS_PORT% --reload"
timeout /t 4 /nobreak >NUL
echo        Pipeline server window opened  (http://127.0.0.1:%SHORTS_PORT%)

:: ── Open browser ─────────────────────────────────────────────────────────────
echo.
echo  Opening UI in browser...
timeout /t 3 /nobreak >NUL
start "" "http://127.0.0.1:%SHORTS_PORT%"

echo.
echo ============================================================
echo   All services started!
echo.
echo   Pipeline UI  : http://127.0.0.1:%SHORTS_PORT%
echo   ComfyUI      : http://127.0.0.1:8188
echo.
echo   Planner: Gemini / DeepSeek hybrid  (see .env)
echo   Close this window anytime — services keep running.
echo ============================================================
echo.
pause

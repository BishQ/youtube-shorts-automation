@echo off
title YouTube Shorts Automation — Stop
color 0C
echo.
echo  Stopping all services...
echo.

:: Kill pipeline server (uvicorn)
taskkill /F /FI "WINDOWTITLE eq Shorts Pipeline*" /T >NUL 2>&1
echo  [done]  Pipeline server stopped.

:: Kill ComfyUI window
taskkill /F /FI "WINDOWTITLE eq ComfyUI*" /T >NUL 2>&1
echo  [done]  ComfyUI stopped.

echo.
echo  All services stopped.
echo.
pause

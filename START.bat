@echo off
set "PROJECT=%~dp0"
powershell -ExecutionPolicy Bypass -File "%PROJECT%scripts\windows\start.ps1" -ProjectRoot "%PROJECT%"

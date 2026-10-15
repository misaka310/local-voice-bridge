@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist "%CD%\LocalVoiceBridge.exe" (
  start "" "%CD%\LocalVoiceBridge.exe" --setup
  exit /b 0
)

powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "%CD%\scripts\setup\setup-gui.ps1"
exit /b %ERRORLEVEL%

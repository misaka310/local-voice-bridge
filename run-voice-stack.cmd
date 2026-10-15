@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PY=%CD%\local-api\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [FAILED] local-api\.venv is missing. Run setup-voice-env.cmd first.
  exit /b 1
)
set "PORT_PID="
for /f %%a in ('powershell -NoProfile -Command "$conn = Get-NetTCPConnection -State Listen -LocalPort 8717 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($conn) { Write-Output $conn }"') do (
  set "PORT_PID=%%a"
)
if defined PORT_PID (
  echo [FAILED] Port 8717 is already in use.
  echo PID: %PORT_PID%
  tasklist /fi "PID eq %PORT_PID%"
  echo Stop the process above, then rerun this file.
  exit /b 1
)
echo Local API URL: http://127.0.0.1:8717
echo Health URL: http://127.0.0.1:8717/health
echo Python path: %PY%
echo runtime=irodori_direct
echo model=irodori-v3
echo cache check:
"%PY%" "%CD%\local-api\scripts\preflight_irodori.py" --strict-cuda --quick || exit /b 1
echo.
echo Starting Local Voice Bridge. Keep this window open.
echo First /v1/speak may wait while Irodori loads into GPU memory.
"%PY%" "%CD%\local-api\server.py"

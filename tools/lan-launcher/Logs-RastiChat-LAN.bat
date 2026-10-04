@echo off
REM Thin wrapper — all real logic lives in RastiChat-LAN-Manager.ps1 (Invoke-CmdLogs).
REM Double-click for a one-shot dump of every service's recent log lines.
REM For a specific service or live-follow mode, run from a terminal instead:
REM   Logs-RastiChat-LAN.bat -Service backend -Follow
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%RastiChat-LAN-Manager.ps1" logs %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%

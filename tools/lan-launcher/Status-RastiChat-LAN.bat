@echo off
REM Thin wrapper — all real logic lives in RastiChat-LAN-Manager.ps1 (Invoke-CmdStatus).
REM Shows which services are running, their PID/port/URL/health/uptime.
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%RastiChat-LAN-Manager.ps1" status %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%

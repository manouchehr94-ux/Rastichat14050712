@echo off
REM Thin wrapper — all real logic lives in RastiChat-LAN-Manager.ps1 (Invoke-CmdRestart).
REM Stops everything, then starts it again (same flow as Stop + Start).
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%RastiChat-LAN-Manager.ps1" restart %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%

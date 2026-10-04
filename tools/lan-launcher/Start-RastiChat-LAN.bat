@echo off
REM Thin wrapper — all real logic lives in RastiChat-LAN-Manager.ps1 (Invoke-CmdStart).
REM Double-click this file. It builds/starts the whole stack, prints LAN URLs,
REM and opens the Widget + Operator Dashboard in your browser.
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%RastiChat-LAN-Manager.ps1" start %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%

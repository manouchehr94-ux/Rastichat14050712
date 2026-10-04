@echo off
REM Thin wrapper — all real logic lives in RastiChat-LAN-Manager.ps1 (Invoke-CmdStop).
REM Stops the Widget/Operator/Platform processes and the Docker containers.
REM Database data is preserved (add -RemoveFirewallRules to also drop the
REM firewall rules this launcher created; see README.md for a full reset).
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%RastiChat-LAN-Manager.ps1" stop %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%

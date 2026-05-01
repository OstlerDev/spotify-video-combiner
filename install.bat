@echo off
REM Double-clickable Windows installer. Bypasses execution policy and runs
REM install.ps1 in PowerShell. Just double-click this file.

setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

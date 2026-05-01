@echo off
REM Double-clickable Windows build wrapper. Bypasses execution policy and
REM runs build_exe.ps1 in PowerShell. Just double-click this file to build
REM dist\svc-gui.exe (the single-file Windows distributable).

setlocal
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build_exe.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-sumx.ps1" %*
if errorlevel 1 (
    echo.
    echo Startup failed. The error above identifies the log or dependency to check.
    pause
    exit /b 1
)

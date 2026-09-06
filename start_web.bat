@echo off
rem One-click launcher for Quanti Web Console (Windows)
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start_web.py %*
) else (
    python start_web.py %*
)

if errorlevel 1 (
    echo.
    echo Failed to start. Please check Python/Node installation.
    pause
)

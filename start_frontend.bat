@echo off
title Zircon - Coding Agent
cd /d "%~dp0"

echo.
echo   Zircon v1.0
echo.
echo   Starting the native app...
echo.

:: Check Python exists
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python is not installed or not in PATH.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: launcher.py registers the package alias dynamically
python "%~dp0frontend\launcher.py"

echo.
echo   App closed.
pause
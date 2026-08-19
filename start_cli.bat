@echo off
title Zircon - Coding Agent (CLI)
cd /d "%~dp0"

echo.
echo   ███████╗██╗██████╗  ██████╗ ██████╗ ███╗   ██╗
echo   ╚══███╔╝██║██╔══██╗██╔════╝██╔═══██╗████╗  ██║
echo     ███╔╝ ██║██████╔╝██║     ██║   ██║██╔██╗ ██║
echo    ███╔╝  ██║██╔══██╗██║     ██║   ██║██║╚██╗██║
echo   ███████╗██║██║  ██║╚██████╗╚██████╔╝██║ ╚████║
echo   ╚══════╝╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝
echo   Zircon v1.0 — Autonomous Coding Agent
echo.

:: Check Python exists
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python is not installed or not in PATH.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install/check dependencies
if not exist "requirements.txt" (
    echo   [ERROR] requirements.txt not found.
    pause
    exit /b 1
)

echo   Checking dependencies...
python -m pip install -q -r requirements.txt 2>nul

:: Launch Zircon CLI TUI
echo.
echo   Starting Zircon CLI...
echo.

python -m zirconAgent %*

echo.
echo   Zircon closed.
pause
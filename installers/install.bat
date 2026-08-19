@echo off
setlocal enabledelayedexpansion

title Zircon Installer
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
if not defined ZIRCON_INSTALL_WORKSPACE set "ZIRCON_INSTALL_WORKSPACE=%CD%"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required and must be in PATH.
    pause
    exit /b 1
)

if not exist "%PROJECT_ROOT%\cli\tui\__init__.py" (
    echo [ERROR] Zircon TUI was not found under "%PROJECT_ROOT%\cli\tui".
    pause
    exit /b 1
)

set "INSTALL_DIR=%USERPROFILE%\.local\bin"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
set "LAUNCHER=%INSTALL_DIR%\zircon.cmd"

(
echo @echo off
echo setlocal
echo set "PROJECT_ROOT=%PROJECT_ROOT%"
echo set "PYTHON=python"
echo if exist "%%PROJECT_ROOT%%\.venv\Scripts\python.exe" set "PYTHON=%%PROJECT_ROOT%%\.venv\Scripts\python.exe"
echo if not exist "%%PROJECT_ROOT%%\cli\tui\__init__.py" ^(
echo     echo [ERROR] Zircon moved after installation. Run install.bat again.
echo     exit /b 1
echo ^)
echo if "%%~1"=="" goto no_args
echo set "FIRST=%%~1"
echo if "%%FIRST:~0,1%%"=="-" goto flags
echo "%%PYTHON%%" "%%PROJECT_ROOT%%\__main__.py" %%*
echo exit /b %%errorlevel%%
echo.
echo :flags
echo "%%PYTHON%%" "%%PROJECT_ROOT%%\__main__.py" "%%CD%%" %%*
echo exit /b %%errorlevel%%
echo.
echo :no_args
echo "%%PYTHON%%" "%%PROJECT_ROOT%%\__main__.py" "%%CD%%"
echo exit /b %%errorlevel%%
) > "%LAUNCHER%"

set "USER_PATH="
for /f "skip=2 tokens=2*" %%A in ('reg query HKCU\Environment /v PATH 2^>nul') do set "USER_PATH=%%B"
echo ;!USER_PATH!; | find /i ";%INSTALL_DIR%;" >nul 2>&1
if errorlevel 1 (
    if defined USER_PATH (
        setx PATH "%INSTALL_DIR%;!USER_PATH!" >nul
    ) else (
        setx PATH "%INSTALL_DIR%" >nul
    )
)

echo.
echo Zircon installed at "%LAUNCHER%".
echo Opening Zircon in a new terminal...
start "Zircon" cmd /k ""%LAUNCHER%" "%ZIRCON_INSTALL_WORKSPACE%""
exit /b 0

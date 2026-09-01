@echo off
setlocal enabledelayedexpansion

title Zircon Installer
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
if not defined ZIRCON_INSTALL_WORKSPACE set "ZIRCON_INSTALL_WORKSPACE=%CD%"

if not exist "%PROJECT_ROOT%\cli\tui\__init__.py" (
    echo [ERROR] Zircon TUI was not found under "%PROJECT_ROOT%\cli\tui".
    exit /b 1
)

echo Detected Windows.
set "PYTHON="
for %%P in (python.exe python3.exe) do (
    if not defined PYTHON (
        for /f "delims=" %%I in ('where %%P 2^>nul') do (
            if not defined PYTHON "%%I" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1 && set "PYTHON=%%I"
        )
    )
)
if not defined PYTHON (
    for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable if sys.version_info.minor in range(10, 100) else '')" 2^>nul') do set "PYTHON=%%I"
)

if not defined PYTHON (
    echo Python 3.10 or newer was not found. Installing Python 3.12...
    set "USE_DIRECT_DOWNLOAD=1"
    where winget >nul 2>&1
    if not errorlevel 1 (
        winget install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements --silent
        if not errorlevel 1 set "USE_DIRECT_DOWNLOAD=0"
    )
    if "!USE_DIRECT_DOWNLOAD!"=="1" (
        where powershell.exe >nul 2>&1
        if errorlevel 1 (
            echo [ERROR] Python could not be installed because neither winget nor PowerShell is available.
            exit /b 1
        )
        set "PYTHON_ARCH=amd64"
        if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PYTHON_ARCH=arm64"
        if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 set "PYTHON_ARCH=exe"
        if "!PYTHON_ARCH!"=="exe" (
            set "PYTHON_INSTALLER=%TEMP%\python-3.12.10.exe"
            set "PYTHON_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10.exe"
        ) else (
            set "PYTHON_INSTALLER=%TEMP%\python-3.12.10-!PYTHON_ARCH!.exe"
            set "PYTHON_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-!PYTHON_ARCH!.exe"
        )
        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest -UseBasicParsing $env:PYTHON_URL -OutFile $env:PYTHON_INSTALLER"
        if errorlevel 1 exit /b 1
        "!PYTHON_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
        if errorlevel 1 exit /b 1
        del /q "!PYTHON_INSTALLER!" >nul 2>&1
    )
    for /f "delims=" %%I in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON=%%I"
    if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if not defined PYTHON (
    echo [ERROR] Python installation completed, but Python 3.10 or newer could not be found.
    exit /b 1
)

echo Using Python: "%PYTHON%"
if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    "%PROJECT_ROOT%\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
    if errorlevel 1 (
        echo Updating the existing virtual environment...
        "%PYTHON%" -m venv --upgrade "%PROJECT_ROOT%\.venv"
        if errorlevel 1 exit /b 1
    )
)
if not exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    echo Creating virtual environment...
    "%PYTHON%" -m venv "%PROJECT_ROOT%\.venv"
    if errorlevel 1 exit /b 1
)
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

echo Installing Zircon dependencies...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 exit /b 1
"%PYTHON%" -m pip install -r "%PROJECT_ROOT%\requirements.txt"
if errorlevel 1 exit /b 1

set "INSTALL_DIR=%USERPROFILE%\.local\bin"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
set "LAUNCHER=%INSTALL_DIR%\zircon.cmd"

(
echo @echo off
echo setlocal
echo set "PROJECT_ROOT=%PROJECT_ROOT%"
echo set "PYTHON=%%PROJECT_ROOT%%\.venv\Scripts\python.exe"
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

rem Register the launcher with Explorer's ShellExecute lookup so typing
rem "zircon" in the address bar can start it without relying on PATH refresh.
set "APP_PATH_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\zircon.exe"
reg add "%APP_PATH_KEY%" /ve /d "%LAUNCHER%" /f >nul 2>&1
if errorlevel 1 (
    echo [WARN] Could not register Zircon for the Explorer address bar.
) else (
    reg add "%APP_PATH_KEY%" /v Path /d "%INSTALL_DIR%" /f >nul 2>&1
)

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
echo Type zircon in a new Explorer address bar to open Zircon.
echo Opening Zircon in a new terminal...
start "Zircon" cmd /k ""%LAUNCHER%" "%ZIRCON_INSTALL_WORKSPACE%""
exit /b 0

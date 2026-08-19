@echo off
setlocal
set "ZIRCON_INSTALL_WORKSPACE=%CD%"
call "%~dp0installers\install.bat"
exit /b %errorlevel%

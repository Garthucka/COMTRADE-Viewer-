@echo off
setlocal EnableExtensions

set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if not exist "%ISCC%" (
    echo Inno Setup Compiler not found.
    echo Install Inno Setup 6 or edit ISCC path in this BAT.
    pause
    exit /b 1
)

"%ISCC%" "comtrade_viewer_installer.iss"
if errorlevel 1 (
    echo Installer build failed.
    pause
    exit /b 1
)

echo Installer created in installer_output
pause

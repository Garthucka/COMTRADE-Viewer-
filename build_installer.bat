@echo off
setlocal EnableExtensions

cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if not exist "%ISCC%" (
    echo Inno Setup Compiler not found.
    exit /b 1
)

echo [1/3] Build application...
call "%~dp0build_comtrade_viewer_safe.bat"
if errorlevel 1 (
    echo Application build failed. Installer will not be created.
    exit /b 1
)

echo [2/3] Check application output...
if not exist "%~dp0dist\comtrade_viewer\comtrade_viewer.exe" (
    echo Expected EXE not found.
    exit /b 1
)

echo [3/3] Build installer...
"%ISCC%" "%~dp0comtrade_viewer_installer.iss"
if errorlevel 1 (
    echo Installer build failed.
    exit /b 1
)

echo Installer created successfully.
pause
exit /b 0
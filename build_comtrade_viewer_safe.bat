@echo off
setlocal EnableExtensions

set "VENV_DIR=.venv_311"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PIP_EXE=%VENV_DIR%\Scripts\pip.exe"
set "PYINSTALLER_EXE=%VENV_DIR%\Scripts\pyinstaller.exe"
set "WINDEPLOYQT=%VENV_DIR%\Lib\site-packages\PyQt6\Qt6\bin\windeployqt.exe"
set "QT_API=PyQt6"
set "MPLBACKEND=QtAgg"

echo [1/6] Remove old virtual environment...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
if errorlevel 1 goto :error

echo [2/6] Create new virtual environment...
python -m venv "%VENV_DIR%"
if errorlevel 1 goto :error

echo [3/6] Upgrade pip tools inside venv...
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error

echo [4/6] Install dependencies into venv...
"%PIP_EXE%" install pyinstaller PyQt6 matplotlib numpy comtrade
if errorlevel 1 goto :error

echo [5/6] Clean old build folders...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [6/6] Build application using spec...
"%PYINSTALLER_EXE%" --noconfirm --clean "comtrade_viewer.spec"
if errorlevel 1 goto :error

if exist "%WINDEPLOYQT%" (
    echo Running windeployqt...
    "%WINDEPLOYQT%" --verbose 0 --compiler-runtime "dist\comtrade_viewer\comtrade_viewer.exe"
) else (
    echo windeployqt.exe not found. Skip this step.
)

echo.
echo Build complete.
echo Output folder: dist\comtrade_viewer
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1

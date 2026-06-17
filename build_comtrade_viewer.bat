@echo off
setlocal
chcp 65001 >nul

set VENV_DIR=.venv_comtrade_build
set QT_API=PyQt6
set MPLBACKEND=QtAgg

echo [1/6] Создание чистого virtual environment...
if exist %VENV_DIR% rmdir /s /q %VENV_DIR%
python -m venv %VENV_DIR%
if errorlevel 1 goto :error

call %VENV_DIR%\Scripts\activate.bat
if errorlevel 1 goto :error

echo [2/6] Обновление pip/setuptools/wheel...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :error

echo [3/6] Установка зависимостей...
pip install pyinstaller PyQt6 matplotlib numpy comtrade
if errorlevel 1 goto :error

echo [4/6] Очистка предыдущей сборки...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [5/6] Сборка onedir через spec...
pyinstaller --noconfirm --clean comtrade_viewer.spec
if errorlevel 1 goto :error

echo [6/6] Попытка дотащить Qt runtime через windeployqt...
set WINDEPLOYQT=%VENV_DIR%\Lib\site-packages\PyQt6\Qt6\bin\windeployqt.exe
if exist "%WINDEPLOYQT%" (
    "%WINDEPLOYQT%" --verbose 0 --compiler-runtime dist\comtrade_viewer\comtrade_viewer.exe
) else (
    echo windeployqt.exe не найден, шаг пропущен.
)

echo.
echo Готово. Проверяйте папку: dist\comtrade_viewer
pause
exit /b 0

:error
echo.
echo Сборка завершилась с ошибкой.
pause
exit /b 1

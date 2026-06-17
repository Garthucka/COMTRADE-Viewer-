Файлы сборки для comtrade_viewer.py

1. comtrade_viewer.spec
   Безопасная onedir-сборка для PyInstaller.
   Включены PyQt6, matplotlib Qt backend и runtime hook.

2. pyinstaller_qt_runtime_hook.py
   На старте EXE фиксирует:
   - QT_API=PyQt6
   - MPLBACKEND=QtAgg

3. build_comtrade_viewer.bat
   Делает полный цикл:
   - создает чистый .venv_comtrade_build
   - ставит зависимости
   - собирает приложение
   - пытается прогнать windeployqt

Рекомендуемый порядок:
- запускать build_comtrade_viewer.bat на Windows;
- тестировать не один exe, а всю папку dist\comtrade_viewer;
- копировать на чистый ПК именно всю папку dist\comtrade_viewer целиком.

Важно:
- Для чистого ПК не используйте onefile, пока не убедитесь, что onedir стабильно работает.
- Если python не найден, установите Python и добавьте его в PATH.

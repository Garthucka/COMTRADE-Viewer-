Как собрать установщик через Inno Setup

1. Установите Inno Setup 6 на Windows.
2. Положите файл comtrade_viewer_installer.iss рядом с папкой dist\comtrade_viewer.
3. При необходимости положите рядом vc_redist.x64.exe, чтобы установщик автоматически ставил Microsoft Visual C++ Redistributable x64.
4. Откройте comtrade_viewer_installer.iss в Inno Setup Compiler и нажмите Build.
5. Готовый установщик появится в папке installer_output.

Что делает скрипт
- Упаковывает всю папку dist\comtrade_viewer.
- Создает ярлык в меню Пуск.
- По выбору создает ярлык на рабочем столе.
- При наличии vc_redist.x64.exe запускает его тихую установку.

Важно
- Перед сборкой установщика сначала проверьте, что dist\comtrade_viewer\comtrade_viewer.exe запускается на машине сборки.
- На тестовый ПК ставьте именно установщик, а не отдельный exe.

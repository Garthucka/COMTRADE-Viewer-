telegram_comtrade_inline_bot_buttons.py

Что добавлено:
- Inline-кнопки выбора каналов вместо команды /plot A0 A1 S0.
- Переключение overlay/parallel кнопками.
- Построение выбранных каналов и всех аналоговых из панели.
- Меню курсоров с кнопками выбора кривой для курсора 1 и 2.
- Ввод времени для выбранного курсора обычным сообщением в мс.
- Выгрузка CSV и показ измерения прямо из панели.

Команды:
/start
/panel
/info
/measure
/exportcsv

Сценарий:
1. Отправьте test.cfg и test.dat.
2. Выполните /panel.
3. Отметьте каналы кнопками.
4. Нажмите "Построить выбранные".
5. Нажмите "Курсоры" и выберите кривую для курсора 1 или 2.
6. Отправьте число времени, например 12.5.
7. Повторите для второго курсора.
8. Нажмите "Измерение" или /measure.

Установка:
pip install python-telegram-bot comtrade matplotlib numpy
set TELEGRAM_BOT_TOKEN=...   (Windows)
python telegram_comtrade_inline_bot_buttons.py


"""
COMTRADE Viewer — вкладки, INFO и база UUID

Расширенный просмотрщик COMTRADE с курсорами на все графики одновременно, измерениями по участкам,
экспортом в CSV, читаемым протоколом .info и выгрузкой в .txt результатов из .info.

Зависимости:
    pip install comtrade PyQt6 matplotlib numpy pyinstaller
"""

from __future__ import annotations

import os
import glob
import uuid
import copy
import math
import csv
# CSV используется для экспорта результатов измерений в удобный табличный формат.
import json
import sys
import traceback
import tempfile
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import matplotlib
matplotlib.use("qtagg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.axes import Axes
from matplotlib.artist import Artist

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QLineEdit,
    QScrollArea,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from comtrade import Comtrade
except ImportError as exc:
    raise SystemExit("Не найдена библиотека 'comtrade'. Установите: pip install comtrade") from exc



# ─── Конвертер .info → читаемый текстовый протокол ──────────────────────────
_INFO_TRIGGER_SOURCE = {0: "Нет", 1: "Внешний", 81: "Внутренний"}
_INFO_TRIGGER_FRONT  = {1: "Передний (нарастающий)", 2: "Задний (спадающий)"}
_INFO_SENSOR_TYPE    = {0: "Ход (линейный)", 1: "Угловой"}
_INFO_CYCLE_TYPE     = {0: "Отключение (О)", 1: "Включение (В)", 2: "О-В-О"}
_INFO_OPERATION_TYPE = {0: "Отключение (О)", 1: "Включение (В)", 2: "О-В-О"}



def normalize_uuid(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(uuid.UUID(text))


def database_path() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "breaker_database.json"


def load_breaker_database(path: Optional[Path] = None) -> dict:
    path = path or database_path()
    if not path.exists():
        return {"schema_version": 1, "breakers": {}}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("breakers"), dict):
        raise ValueError("Некорректная структура базы выключателей")
    records = {}
    for key, value in data["breakers"].items():
        if not isinstance(value, dict):
            raise ValueError("Некорректная запись в базе выключателей")
        records[normalize_uuid(key)] = value
    data["breakers"] = records
    return data


def save_breaker_database(data: dict, path: Optional[Path] = None) -> None:
    path = path or database_path()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            tmp_path = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def get_info_metadata(data: dict) -> dict:
    ms = data.get("meas_sett") or {}
    ms = ms if isinstance(ms, dict) else {}
    recalc = ms.get("recalc_settings")
    recalc = recalc if isinstance(recalc, dict) else {}
    device = data.get("device_info") or {}
    device = device if isinstance(device, dict) else {}
    angular = str(ms.get("sensor_type")) == "1"
    raw_unit = str(recalc.get("unit_out_str") or "").strip()
    unit = {"mm": "мм", "deg": "°", "degree": "°", "rad": "рад", "m": "м", "cm": "см"}.get(raw_unit.lower(), raw_unit)
    unit = unit or ("°" if angular else "мм")
    return dict(device=str(device.get("device") or ""), serial=str(device.get("serial") or ""),
                ver=str(device.get("ver") or ""), table_uuid=str(recalc.get("table_uuid") or "").strip(),
                initial_value=recalc.get("initial_value"), unit_out_str=raw_unit,
                recalc_present=isinstance(ms.get("recalc_settings"), dict), value_unit=unit,
                speed_unit="м/с" if unit == "мм" else unit + "/с",
                unit_source="recalc_settings.unit_out_str" if raw_unit else "meas_sett.sensor_type")


def get_breaker_record(database: dict, table_uuid: str) -> dict:
    try:
        key = normalize_uuid(table_uuid)
    except ValueError:
        return {}
    return database.get("breakers", {}).get(key, {})


def format_info_metadata(data: dict, database: Optional[dict] = None) -> str:
    m = get_info_metadata(data)
    record = get_breaker_record(database or {}, m["table_uuid"])
    lines = ["СВЕДЕНИЯ О ПРИБОРЕ", f"  Прибор: {m['device'] or '—'}",
             f"  Серийный номер: {m['serial'] or '—'}", f"  Версия ПО: {m['ver'] or '—'}", "",
             "ВЫКЛЮЧАТЕЛЬ И ПЕРЕСЧЁТ", f"  Выключатель: {record.get('name') or 'Не найден в базе'}",
             f"  UUID выключателя/таблицы: {m['table_uuid'] or '—'}",
             f"  Напряжение, кВ: {record.get('voltage_kv') if record.get('voltage_kv') is not None else '—'}",
             f"  Настройки пересчёта: {'Есть' if m['recalc_present'] else 'Нет'}",
             f"  Начальное значение: {m['initial_value'] if m['initial_value'] is not None else '—'}",]
    return "\n".join(lines)


class BreakerEditDialog(QDialog):
    def __init__(self, parent, table_uuid: str, record: dict):
        super().__init__(parent)
        self.action = "cancel"
        self.setWindowTitle("Выключатель по UUID")
        self.resize(530, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("UUID выключателя / таблицы пересчёта"))
        uuid_edit = QLineEdit(table_uuid)
        uuid_edit.setReadOnly(True)
        layout.addWidget(uuid_edit)
        layout.addWidget(QLabel("Наименование выключателя"))
        self.name_edit = QLineEdit(str(record.get("name") or ""))
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Номинальное напряжение, кВ (необязательно)"))
        v = record.get("voltage_kv")
        self.voltage_edit = QLineEdit("" if v is None else str(v))
        layout.addWidget(self.voltage_edit)
        layout.addWidget(QLabel("Примечание"))
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlainText(str(record.get("comment") or ""))
        layout.addWidget(self.comment_edit)
        row = QHBoxLayout()
        for caption, slot in [("Сохранить", self.save), ("Удалить запись", self.delete), ("Отмена", self.reject)]:
            button = QPushButton(caption)
            button.clicked.connect(slot)
            if caption == "Удалить запись":
                button.setEnabled(bool(record))
            row.addWidget(button)
        layout.addLayout(row)

    def save(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Проверка", "Введите наименование выключателя")
            return
        try:
            raw = self.voltage_edit.text().strip().replace(",", ".")
            self.voltage = float(raw) if raw else None
            if self.voltage is not None and (not math.isfinite(self.voltage) or self.voltage <= 0):
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Проверка", "Напряжение должно быть положительным числом")
            return
        self.action = "save"
        self.accept()

    def delete(self):
        if QMessageBox.question(self, "Удаление", "Удалить запись выключателя?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.action = "delete"
            self.accept()


def convert_info_json_to_report(data: dict, source_path: str = "", speed_unit: str = "м/с", breaker_database: Optional[dict] = None) -> str:
    """Конвертирует распарсенный JSON из .info файла в читаемый текстовый протокол."""
    from datetime import datetime, timezone

    ms = data.get("meas_sett", {})
    cd = data.get("calc_data", {})
    operations = cd.get("operations", [])

    ts_raw = data.get("timestamp")
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except Exception:
        ts = "—"

    lines: list[str] = []

    def sep(char="─", width=62):
        lines.append(char * width)

    sep("═")
    lines.append("  ПРОТОКОЛ ИСПЫТАНИЯ ВЫКЛЮЧАТЕЛЯ")
    sep("═")
    lines.append(f"  Файл          : {source_path}")
    lines.append(f"  Дата и время  : {data.get('timestamp_str', '—')}  ({ts})")
    lines.append(f"  Версия формата: {data.get('version', '—')}")
    lines.append("")

    sep()
    lines.extend(format_info_metadata(data, breaker_database).splitlines())
    lines.append("")
    lines.append("  НАСТРОЙКИ ИЗМЕРЕНИЯ")
    sep()
    lines.append(f"  Триггер            : {_INFO_TRIGGER_SOURCE.get(ms.get('trigger_source'), ms.get('trigger_source', '—'))}")
    lines.append(f"  Фронт триггера     : {_INFO_TRIGGER_FRONT.get(ms.get('trigger_front'), ms.get('trigger_front', '—'))}")
    lines.append(f"  Тип датчика        : {_INFO_SENSOR_TYPE.get(ms.get('sensor_type'), ms.get('sensor_type', '—'))}")
    lines.append(f"  Тип цикла          : {_INFO_CYCLE_TYPE.get(ms.get('cycle_type'), ms.get('cycle_type', '—'))}")
    lines.append(f"  Время откл.        : {ms.get('open', '—')} мс")
    lines.append(f"  Время вкл.         : {ms.get('close', '—')} мс")
    lines.append(f"  Пауза откл.        : {ms.get('open_pause', '—')} мс")
    lines.append(f"  Пауза вкл.         : {ms.get('close_pause', '—')} мс")
    c_rep = ms.get("cycle_repeat")
    lines.append(f"  Повтор цикла       : {c_rep} мс ({c_rep / 1000:.1f} с)" if c_rep is not None else "  Повтор цикла       : —")
    lines.append(f"  Длит. измерения    : {ms.get('meas_dur', '—')} мс")
    lines.append("")

    meta = get_info_metadata(data)
    val_unit = meta["value_unit"]
    speed_unit = meta["speed_unit"]

    valid_ops = [op for op in operations if op.get("p_switch_dur") is not None]
    for idx, op in enumerate(valid_ops):
        sep()
        lines.append(f"  ОПЕРАЦИЯ #{idx + 1}  —  {_INFO_OPERATION_TYPE.get(op.get('operation_type'), '?')}")
        sep()
        lines.append(f"  Время коммутации (общее)      : {op['p_switch_dur'] / 1000:.3f} мс")
        lines.append(f"  Макс. разновременность        : {(op.get('p_max_desync') or 0) / 1000:.3f} мс")
        lines.append(f"  Полный ход                     : {op.get('s_full', '—')} {val_unit}")
        lines.append(f"  Перелёт                        : {op.get('s_overtravel', '—')} {val_unit}")
        lines.append(f"  Отскок                         : {op.get('s_rebound', '—')} {val_unit}")
        lines.append(f"  Отскок СВ                      : {op.get('s_rebound_cb', '—')} {val_unit}")
        lines.append(f"  Вжим                           : {op.get('s_react_contactstroke', '—')} {val_unit}")
        lines.append(f"  Ход после размыкания           : {op.get('s_swipe_afterstroke', '—')} {val_unit}")
        lines.append(f"  Средняя скорость               : {op.get('s_avg_speed', '—')} {speed_unit}")
        lines.append("")

        poles = op.get("poles", [])
        if any(p for p in poles):
            labels = ["ABC"[i] if i < 3 else str(i + 1) for i in range(len(poles))]
            col_w = 12
            header = f"  {'Параметр':<37}" + "".join(f"{'Полюс ' + labels[i]:>{col_w}}" for i in range(len(poles)))
            lines.append(header)
            lines.append("  " + "─" * (37 + col_w * len(poles)))

            def _row(label, key, fmt_fn):
                vals = [fmt_fn(p[key]) if isinstance(p, dict) and p.get(key) is not None else "—" for p in poles]
                return f"  {label:<37}" + "".join(f"{v:>{col_w}}" for v in vals)

            lines.append(_row("Время коммутации, мс",                    "p_switch_dur", lambda v: f"{v / 1000:.3f}"))
            lines.append(_row("Разновременность, мс",                    "p_desync",     lambda v: f"{v / 1000:.3f}"))
            lines.append(_row("Длит. вибрации, мс",                      "p_vibr",       lambda v: f"{v / 1000:.3f}"))
            lines.append(_row(f"Амплитуда вибрации, {val_unit}",         "s_vibr",       str))
            lines.append(_row(f"Разность по ходу, {val_unit}",           "s_desync",     str))
        lines.append("")

    if not valid_ops:
        lines.append("  (Нет данных об операциях)")
        lines.append("")

    #sep()
    #lines.append("  СЛУЖЕБНАЯ ИНФОРМАЦИЯ")
    #sep()
    #lines.append(f"  Кол-во операций в файле   : {cd.get('operations_count', '—')}")
    #lines.append(f"  Длина DAQ-буфера          : {data.get('daq_data_len', '—')} байт")
    #lines.append(f"  Кол-во отсчётов DAQ       : {data.get('daq_data_count', '—')}")
    #lines.append(f"  Размер .dat файла         : {data.get('file_dat_size', '—')} байт  (CRC: {data.get('file_dat_crc', '—')})")
    #lines.append(f"  Размер .cfg файла         : {data.get('file_cfg_size', '—')} байт  (CRC: {data.get('file_cfg_crc', '—')})")
    sep("═")

    return "\n".join(lines)



def convert_drm_json_to_report(data: dict, source_path: str = "") -> str:
    """Конвертирует JSON типа DRM (Dynamic Resistance Measurement) в читаемый протокол."""
    from datetime import datetime, timezone

    ts_raw = data.get("timestamp")
    try:
        ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    except Exception:
        ts = "—"

    ms = data.get("microohm_settings", {})
    mr = data.get("microohm_results", {})
    cal = data.get("calibrations", {})

    MODE_MAP = {
        0: "Одиночный",
        1: "Непрерывный",
        2: "По триггеру",
        4: "Автоматический",
    }
    TRIGGER_MAP = {0: "Нет", 1: "Внешний", 2: "Внутренний"}
    SENSOR_MAP  = {0: "Ход (линейный)", 1: "Угловой"}

    lines: list[str] = []

    def sep(char="─", width=62):
        lines.append(char * width)

    sep("═")
    lines.append("  ПРОТОКОЛ DRM — ДИНАМИЧЕСКОЕ СОПРОТИВЛЕНИЕ КОНТАКТОВ")
    sep("═")
    lines.append(f"  Файл          : {source_path}")
    lines.append(f"  Дата и время  : {data.get('timestamp_str', '—')}  ({ts})")
    lines.append(f"  Версия формата: {data.get('version', '—')}")
    lines.append(f"  Тип измерения : {str(data.get('type') or '—').upper()}")
    lines.append("")

    sep()
    lines.append("  НАСТРОЙКИ МИКРООММЕТРА")
    sep()
    lines.append(f"  Режим работы         : {MODE_MAP.get(ms.get('mode'), str(ms.get('mode', '—')))}")
    lines.append(f"  Ток тестовый, А      : {ms.get('current', '—')}")
    lines.append(f"  Период измерения, мс : {ms.get('period', '—')}")
    lines.append(f"  Источник тока, А     : {ms.get('source', '—')}")
    lines.append(f"  Канал                : {ms.get('channel', '—')}")
    lines.append(f"  Триггер              : {TRIGGER_MAP.get(ms.get('trigger'), str(ms.get('trigger', '—')))}")
    lines.append(f"  Зажимы (clamps)      : {ms.get('clamps', '—')}")
    lines.append("")

    sep()
    lines.append("  РЕЗУЛЬТАТЫ ИЗМЕРЕНИЯ")
    sep()
    res_val = mr.get('resistance')
    res_str = f"{res_val:.6f} мОм" if res_val is not None else "—"
    lines.append(f"  Сопротивление        : {res_str}")
    volt_val = mr.get('voltage')
    lines.append(f"  Напряжение, мВ       : {volt_val if volt_val is not None else '—'}")
    curr_val = mr.get('current')
    lines.append(f"  Ток измеренный, А    : {curr_val if curr_val is not None else '—'}")
    lines.append(f"  Источник тока, А     : {mr.get('source', '—')}")
    lines.append(f"  Позиция              : {mr.get('position', '—')}")
    lines.append("")

    # Калибровки
    if cal and any(cal.get(k) for k in ("a", "b", "skew")):
        sep()
        lines.append("  КАЛИБРОВОЧНЫЕ КОЭФФИЦИЕНТЫ")
        sep()
        ch_names = ["Ток (А)", "Напряжение (В)", "Доп.1", "Доп.2", "Угол"]
        a_vals = cal.get("a", [])
        b_vals = cal.get("b", [])
        skew_vals = cal.get("skew", [])
        col_w = 14
        lines.append(f"  {'Канал':<20} {'a (масштаб)':>{col_w}} {'b (смещение)':>{col_w}} {'skew':>{col_w}}")
        lines.append("  " + "─" * (20 + col_w * 3))
        for i, name in enumerate(ch_names):
            a = f"{a_vals[i]:.2e}" if i < len(a_vals) else "—"
            b = f"{b_vals[i]:.4f}" if i < len(b_vals) else "—"
            s = f"{skew_vals[i]:.4f}" if i < len(skew_vals) else "—"
            lines.append(f"  {name:<20} {a:>{col_w}} {b:>{col_w}} {s:>{col_w}}")
        lines.append("")

    #sep()
    #lines.append("  СЛУЖЕБНАЯ ИНФОРМАЦИЯ")
    #sep()
    #lines.append(f"  Тип датчика DRM       : {SENSOR_MAP.get(data.get('drm_sensor_type'), str(data.get('drm_sensor_type', '—')))}")
    #lines.append(f"  Частота дискр., Гц    : {data.get('sample_rate_hz', '—')}")
    #lines.append(f"  Кол-во отсчётов       : {data.get('samples_count', data.get('daq_data_count', '—'))}")
    #lines.append(f"  Длина DAQ-буфера      : {data.get('daq_data_len', '—')} байт")
    #lines.append(f"  Размер .dat файла     : {data.get('file_dat_size', '—')} байт  (CRC: {data.get('file_dat_crc', '—')})")
    #lines.append(f"  Размер .cfg файла     : {data.get('file_cfg_size', '—')} байт  (CRC: {data.get('file_cfg_crc', '—')})")
    sep("═")

    return "\n".join(lines)


# Описание одной построенной кривой.
# Здесь хранится все, что нужно для повторной идентификации канала,
# привязки курсоров и вычислений по участку.
@dataclass
class CurveRef:
    uid: str
    ax: Axes
    line: Line2D
    channel_type: str
    channel_index: int
    label: str
    time_ms: np.ndarray
    values: np.ndarray


# Состояние одного измерительного курсора.
# Курсор хранит не только координаты, но и ссылку на кривую и индекс отсчета,
# чтобы расчеты выполнялись по реальным данным COMTRADE, а не по экранной геометрии.
@dataclass
class CursorState:
    name: str
    color: str
    ax: Optional[Axes] = None
    curve: Optional[CurveRef] = None
    sample_index: Optional[int] = None
    x_ms: Optional[float] = None
    y_value: Optional[float] = None
    artists: list[Artist] = field(default_factory=list)

    def clear(self) -> None:
        # Полный сброс состояния курсора и удаление всех его графических объектов.
        self.ax = None
        self.curve = None
        self.sample_index = None
        self.x_ms = None
        self.y_value = None
        self.remove_artists()

    def remove_artists(self) -> None:
        # Удаляем линии, маркеры и подписи, уже добавленные на Matplotlib-ось.
        for artist in self.artists:
            try:
                artist.remove()
            except Exception:
                pass
        self.artists.clear()

    @property
    def is_set(self) -> bool:
        return (
            self.ax is not None
            and self.curve is not None
            and self.sample_index is not None
            and self.x_ms is not None
            and self.y_value is not None
        )


# Холст Matplotlib, встроенный в интерфейс PyQt6.
# Используется штатный Qt canvas, поэтому координаты мыши и данных согласованы корректно.
class ComtradePlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent: Optional[QWidget] = None):
        self.fig = Figure(figsize=(12, 7), dpi=100, constrained_layout=True)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setFocus()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


# Главное окно программы.
# В одном классе объединены загрузка COMTRADE, построение графиков,
# логика курсоров, вычисления по участку и экспорт результатов.
class ComtradeViewer(QMainWindow):
    TRANSLATIONS = {
        "ru": {
            "app_title": "COMTRADE Viewer",
            "open_cfg_file": "▶ Открыть CFG файл",
            "file_not_loaded": "Файл не загружен",
            "view_mode": "Режим просмотра",
            "mode_overlay": "Наложение графиков",
            "mode_parallel": "Параллельный просмотр",
            "channels_to_display": "Каналы для отображения",
            "plotting": "Построение",
            "plot_selected": "▲ Отобразить выбранные",
            "plot_all_analog": "▦ Все аналоговые",
            "measurement_mode": "Режим измерения",
            "measure_auto": "Авто: ближайшая кривая на активной оси",
            "measure_single": "Только одна выбранная кривая",
            "no_curves": "Нет построенных кривых",
            "actions_export": "Измерения и экспорт",
            "export_csv": "⊡ Экспорт измерений в CSV",
            "clear_both_cursors": "✕ Очистить оба курсора",
            "hint": "Подсказка",
            "hint_text": "ЛКМ: установить или удалить Курсор 1\nПКМ: установить или удалить Курсор 2",
            "measurements": "Измерения",
            "measurements_tab": "Измерения",
            "info_tab": "Результаты",
            "info_group_title": "Содержимое INFO",
            "info_not_found": "Файл .info не найден рядом с COMTRADE файлом.",
            "info_empty": "Файл .info пустой.",
            "info_read_error": "Не удалось прочитать файл .info:\n{error}",
            "info_header": "Файл: {path}\n\n",
            "ready": "Готово",
            "select_cfg_file": "Выберите COMTRADE CFG файл",
            "cfg_filter": "COMTRADE CFG (*.cfg);;Все файлы (*.*)",
            "error": "Ошибка",
            "dat_file_not_found": "DAT файл не найден:\n{path}",
            "load_failed": "Не удалось загрузить файл:\n{error}",
            "loaded_status": "Загружен файл: {name} | кодировка: {encoding} | отсчетов: {samples}",
            "file_label": "≡ Файл: {source}",
            "device_label": "Устройство: {device}",
            "analog_channels": "Аналоговые каналы: {count}",
            "status_channels": "Дискретные каналы: {count}",
            "duration_ms": "Длительность измерения: {duration:.1f} мс",
            "load_and_select": "Загрузите файл и выберите каналы",
            "time_axis": "Время",
            "value_axis": "Значение",
            "warn_load_first": "Сначала загрузите COMTRADE файл.",
            "warn_select_channel": "Выберите хотя бы один канал.",
            "warn_no_analog": "В файле нет аналоговых каналов.",
            "plot_done": "График построен",
            "plot_all_done": "Построены все аналоговые каналы",
            "plot_error": "Ошибка построения графика:\n{error}",
            "overlay_title": "Наложение графиков — {station}",
            "parallel_title": "Параллельный просмотр — {station}",
            "time_ms_axis": "Время, мс",
            "measure_pick_failed": "Не удалось определить кривую для измерения",
            "cursor_measurements": "ИЗМЕРЕНИЯ КУРСОРОВ",
            "cursor_not_set": "{icon} {name}: не установлен",
            "cursor_block": "{icon} {name}\n  Канал: {label}\n  T: {time:.2f} мс\n  Y: {value:.2f}",
            "binding_mode_title": "Режим привязки",
            "binding_mode_line": "  Режим: {mode}",
            "binding_selected_curve": "  Выбранная кривая: {curve}",
            "binding_mode_auto_short": "Авто",
            "binding_mode_single_short": "Только одна кривая",
            "curve_not_selected": "не выбрана",
            "segment_need_same_curve": "Участок между курсорами\n  Для расчёта оба курсора должны быть на одной и той же кривой.",
            "segment_title": "Участок между курсорами",
            "segment_channel": "  Канал: {label}",
            "segment_dt": "  ΔT: {delta_t:.2f} мс",
            "segment_dy": "  ΔY: {delta_y:.2f}",
            "segment_avg_speed": "  Средняя скорость: {speed:.2f} м/с",
            "save_csv": "Сохранить CSV",
            "csv_filter": "CSV файлы (*.csv)",
            "csv_saved": "CSV сохранён: {path}",
            "csv_save_failed": "Не удалось сохранить CSV:\n{error}",
            "save_info_report": "≡ Сохранить протокол .txt",
            "info_report_filter": "Текстовые файлы (*.txt)",
            "info_report_saved": "Протокол сохранён: {path}",
            "info_report_save_failed": "Не удалось сохранить протокол:\n{error}",
            "info_no_file": "Сначала загрузите COMTRADE файл с .info.",

            "info_keys": {
                "s_full": "Полный ход",
                "s_overtravel": "Перелет",
                "s_rebound": "Отскок",
                "s_rebound_cb": "Отскок СВ",
                "s_avg_speed": "Средняя скорость",
                "poles": "Данные по фазам",

            }
        },
        "en": {
            "app_title": "COMTRADE Viewer",
            "open_cfg_file": "▶ Open CFG file",
            "file_not_loaded": "No file loaded",
            "view_mode": "View mode",
            "mode_overlay": "Overlay plots",
            "mode_parallel": "Parallel view",
            "channels_to_display": "Channels to display",
            "plotting": "Plotting",
            "plot_selected": "▲ Plot selected",
            "plot_all_analog": "▦ All analog",
            "measurement_mode": "Measurement mode",
            "measure_auto": "Auto: nearest curve on active axis",
            "measure_single": "Only one selected curve",
            "no_curves": "No plotted curves",
            "actions_export": "Measurements and export",
            "export_csv": "⊡ Export measurements to CSV",
            "clear_both_cursors": "✕ Clear both cursors",
            "hint": "Hint",
            "hint_text": "LMB: set or remove Cursor 1\nRMB: set or remove Cursor 2",
            "measurements": "Measurements",
            "measurements_tab": "Measurements",
            "info_tab": "INFO",
            "info_group_title": "INFO content",
            "info_not_found": ".info file was not found near the COMTRADE file.",
            "info_empty": ".info file is empty.",
            "info_read_error": "Failed to read .info file:\n{error}",
            "info_header": "File: {path}\n\n",
            "ready": "Ready",
            "select_cfg_file": "Select COMTRADE CFG file",
            "cfg_filter": "COMTRADE CFG (*.cfg);;All files (*.*)",
            "error": "Error",
            "dat_file_not_found": "DAT file not found:\n{path}",
            "load_failed": "Failed to load file:\n{error}",
            "loaded_status": "Loaded file: {name} | encoding: {encoding} | samples: {samples}",
            "file_label": "≡ File: {source}",
            "device_label": "Device: {device}",
            "analog_channels": "Analog channels: {count}",
            "status_channels": "Digital channels: {count}",
            "duration_ms": "Record duration: {duration:.1f} ms",
            "load_and_select": "Load a file and select channels",
            "time_axis": "Time",
            "value_axis": "Value",
            "warn_load_first": "Load a COMTRADE file first.",
            "warn_select_channel": "Select at least one channel.",
            "warn_no_analog": "There are no analog channels in the file.",
            "plot_done": "Plot created",
            "plot_all_done": "All analog channels plotted",
            "plot_error": "Plotting error:\n{error}",
            "overlay_title": "Overlay plots — {station}",
            "parallel_title": "Parallel view — {station}",
            "time_ms_axis": "Time, ms",
            "measure_pick_failed": "Failed to determine the measurement curve",
            "cursor_measurements": "CURSOR MEASUREMENTS",
            "cursor_not_set": "{icon} {name}: not set",
            "cursor_block": "{icon} {name}\n  Channel: {label}\n  T: {time:.2f} ms\n  Y: {value:.2f}",
            "binding_mode_title": "Binding mode",
            "binding_mode_line": "  Mode: {mode}",
            "binding_selected_curve": "  Selected curve: {curve}",
            "binding_mode_auto_short": "Auto",
            "binding_mode_single_short": "Single curve",
            "curve_not_selected": "not selected",
            "segment_need_same_curve": "Segment between cursors\n  Both cursors must belong to the same curve for calculation.",
            "segment_title": "Segment between cursors",
            "segment_channel": "  Channel: {label}",
            "segment_dt": "  ΔT: {delta_t:.2f} ms",
            "segment_dy": "  ΔY: {delta_y:.2f}",
            "segment_avg_speed": "  Average speed: {speed:.2f} m/s",
            "save_csv": "Save CSV",
            "csv_filter": "CSV files (*.csv)",
            "csv_saved": "CSV saved: {path}",
            "csv_save_failed": "Failed to save CSV:\n{error}",
            "save_info_report": "≡ Save report .txt",
            "info_report_filter": "Text files (*.txt)",
            "info_report_saved": "Report saved: {path}",
            "info_report_save_failed": "Failed to save report:\n{error}",
            "info_no_file": "Load a COMTRADE file with .info first.",
        },
    }

    def __init__(self) -> None:
        # Инициализация окна и основных рабочих структур.
        super().__init__()
        self.language = "ru"
        self.setWindowTitle(self.tr("app_title"))
        self.resize(1750, 980)

        self.comtrade_file: Optional[Comtrade] = None
        self.current_cfg_path: Optional[Path] = None
        self.current_info_path: Optional[Path] = None
        self.plot_mode = "parallel"
        self.curves: list[CurveRef] = []
        self.annotation_artists: list[Artist] = []
        self.cursor1 = CursorState(name="Курсор 1", color="red")
        self.cursor2 = CursorState(name="Курсор 2", color="blue")
        self._last_selected_channels: list[tuple[str, int]] = []

        self.info_data: dict[str, Any] = {}
        self._info_report_ready = False
        self._db_error = ""
        try:
            self.breaker_database = load_breaker_database()
        except Exception as exc:
            self.breaker_database = {"schema_version": 1, "breakers": {}}
            self._db_error = str(exc)
        self._init_ui()
        if self._db_error:
            QMessageBox.warning(self, "База выключателей", self._db_error +
                "\nСохранение базы заблокировано, чтобы не перезаписать повреждённый файл.")
        self._connect_canvas_events()
        self._update_measurement_info()
        self.clear_plot(keep_message=True)

    def _init_ui(self) -> None:
        # Создание интерфейса.
        # Левая часть отведена под управление и текстовую информацию,
        # правая часть — под график и стандартную панель Matplotlib.
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Основной разделитель позволяет менять ширину боковой панели и области графика.
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.main_splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.open_btn = QPushButton(self.tr("open_cfg_file"))
        self.open_btn.clicked.connect(self.open_file)
        left_layout.addWidget(self.open_btn)

        self.left_tabs = QTabWidget()
        left_layout.addWidget(self.left_tabs, 1)
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        settings_layout.addWidget(self.left_splitter)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        left_top = QWidget()
        top_layout = QVBoxLayout(left_top)
        settings_scroll.setWidget(left_top)
        self.left_splitter.addWidget(settings_scroll)
        self.info_label = QLabel(self.tr("file_not_loaded"))
        self.info_label.setWordWrap(True)
        self.info_label.setFont(QFont("Arial", 9))
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top_layout.addWidget(self.info_label)

        mode_group = QGroupBox(self.tr("view_mode"))
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.tr("mode_overlay"), "overlay")
        self.mode_combo.addItem(self.tr("mode_parallel"), "parallel")
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        top_layout.addWidget(mode_group)

        channels_group = QGroupBox(self.tr("channels_to_display"))
        channels_layout = QVBoxLayout(channels_group)
        self.channel_list = QListWidget()
        self.channel_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        channels_layout.addWidget(self.channel_list)
        top_layout.addWidget(channels_group, 1)

        plot_buttons_group = QGroupBox(self.tr("plotting"))
        plot_buttons_layout = QVBoxLayout(plot_buttons_group)
        self.plot_btn = QPushButton(self.tr("plot_selected"))
        self.plot_btn.clicked.connect(self.plot_selected_channels)
        plot_buttons_layout.addWidget(self.plot_btn)
        self.plot_all_btn = QPushButton(self.tr("plot_all_analog"))
        self.plot_all_btn.clicked.connect(self.plot_all_analog)
        plot_buttons_layout.addWidget(self.plot_all_btn)
        top_layout.addWidget(plot_buttons_group)

        measurement_group = QGroupBox(self.tr("measurement_mode"))
        measurement_layout = QVBoxLayout(measurement_group)
        self.measure_mode_combo = QComboBox()
        self.measure_mode_combo.addItem(self.tr("measure_auto"), "auto")
        self.measure_mode_combo.addItem(self.tr("measure_single"), "single")
        measurement_layout.addWidget(self.measure_mode_combo)

        self.measure_curve_combo = QComboBox()
        self.measure_curve_combo.addItem(self.tr("no_curves"), None)
        measurement_layout.addWidget(self.measure_curve_combo)
        top_layout.addWidget(measurement_group)

        actions_group = QGroupBox(self.tr("actions_export"))
        actions_layout = QVBoxLayout(actions_group)
        self.export_csv_btn = QPushButton(self.tr("export_csv"))
        self.export_csv_btn.clicked.connect(self.export_measurements_csv)
        actions_layout.addWidget(self.export_csv_btn)

        self.clear_cursor_btn = QPushButton(self.tr("clear_both_cursors"))
        self.clear_cursor_btn.clicked.connect(lambda checked=False: self.clear_cursors())
        actions_layout.addWidget(self.clear_cursor_btn)
        self.edit_breaker_btn = QPushButton("Выключатель по UUID")
        self.edit_breaker_btn.clicked.connect(self.edit_breaker_record)
        actions_layout.addWidget(self.edit_breaker_btn)
        top_layout.addWidget(actions_group)

        help_group = QGroupBox(self.tr("hint"))
        help_layout = QVBoxLayout(help_group)
        help_label = QLabel(
            "ЛКМ: установить или удалить Курсор 1\n"
            "ПКМ: установить или удалить Курсор 2"
        )
        help_label.setWordWrap(True)
        help_layout.addWidget(help_label)
        top_layout.addWidget(help_group)

        top_layout.addStretch(1)

        measure_info_group = QGroupBox(self.tr("measurements"))
        measure_info_layout = QVBoxLayout(measure_info_group)
        self.cursor_info = QTextEdit()
        self.cursor_info.setReadOnly(True)
        self.cursor_info.setFont(QFont("Consolas", 9))
        measure_info_layout.addWidget(self.cursor_info)

        self.cursor_info.setMinimumHeight(140)
        self.left_splitter.addWidget(measure_info_group)
        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 2)
        self.left_splitter.setSizes([570, 270])

        info_tab = QWidget()
        info_tab_layout = QVBoxLayout(info_tab)
        info_tab_layout.setContentsMargins(0, 0, 0, 0)

        info_group = QGroupBox(self.tr("info_group_title"))
        info_group_layout = QVBoxLayout(info_group)
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setFont(QFont("Consolas", 9))
        self.info_text.setPlainText(self.tr("info_not_found"))
        info_group_layout.addWidget(self.info_text)
        info_tab_layout.addWidget(info_group)

        self.save_info_btn = QPushButton(self.tr("save_info_report"))
        self.save_info_btn.clicked.connect(self.save_info_report)
        info_tab_layout.addWidget(self.save_info_btn)

        self.left_tabs.addTab(settings_tab, "Настройки и курсоры")
        self.left_tabs.addTab(info_tab, "Результаты INFO")
        self.bottom_tabs = self.left_tabs  # Совместимость со старым именем.
        self.save_info_btn.setEnabled(False)
        self.info_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.channel_list.setMinimumHeight(110)
        self.channel_list.setMaximumHeight(240)
        self.measure_mode_combo.currentIndexChanged.connect(lambda _: self._update_measurement_info())
        self.measure_curve_combo.currentIndexChanged.connect(lambda _: self._update_measurement_info())

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.canvas = ComtradePlotCanvas(self)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)

        self.main_splitter.addWidget(left_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([480, 1220])

        self.statusBar().showMessage(self.tr("ready"))

    def _connect_canvas_events(self) -> None:
        # Подключаем обработчик кликов мыши по графику.
        self.canvas.mpl_connect("button_press_event", self.on_canvas_click)

    def _get_attr(self, *names: str, default: Any = None) -> Any:
        # У разных реализаций библиотеки comtrade имена атрибутов могут отличаться.
        # Этот метод позволяет безопасно пробовать несколько вариантов имени.
        if self.comtrade_file is None:
            return default
        for name in names:
            if hasattr(self.comtrade_file, name):
                return getattr(self.comtrade_file, name)
        return default

    def tr(self, key: str, **kwargs: Any) -> str:
        lang_pack = self.TRANSLATIONS.get(self.language, self.TRANSLATIONS["ru"])
        value = lang_pack.get(key, key)
        return value.format(**kwargs) if kwargs else value

    def _base_record_name(self, path: Path) -> str:
        match = re.match(r"^(.*?)(?:-\d+)?$", path.stem)
        return match.group(1) if match else path.stem

    def _find_related_info_file(self, cfg_path: Path) -> Optional[Path]:
        candidates: list[Path] = []
        direct_candidate = next((p for p in cfg_path.parent.iterdir() if p.is_file() and p.stem == cfg_path.stem and p.suffix.lower() == ".info"), cfg_path.with_suffix(".info"))
        if direct_candidate.exists():
            candidates.append(direct_candidate)

        base_name = self._base_record_name(cfg_path)
        candidates.extend(sorted(cfg_path.parent.glob(f"{glob.escape(base_name)}*.info")))

        seen: set[str] = set()
        unique_candidates: list[Path] = []
        for candidate in candidates:
            resolved = str(candidate.resolve())
            if resolved not in seen and candidate.exists():
                seen.add(resolved)
                unique_candidates.append(candidate)
        return unique_candidates[0] if unique_candidates else None

    def _localize_info_key(self, key: str) -> str:
        info_keys = self.TRANSLATIONS.get(self.language, {}).get("info_keys", {})

        normalized_key = key.strip()
        normalized_key = normalized_key.strip('"').strip("'").strip()
        normalized_key = normalized_key.replace("“", '"').replace("”", '"')
        normalized_key = normalized_key.replace("‘", "'").replace("’", "'")
        normalized_key = normalized_key.strip('"').strip("'").strip()

        return info_keys.get(normalized_key, normalized_key)

    def _localize_info_text(self, text: str) -> str:
        lines: list[str] = []

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            if not stripped:
                lines.append("")
                continue

            matched = False
            for separator in (":", "=", " - "):
                if separator in line:
                    left, right = line.split(separator, 1)
                    left_clean = left.strip()
                    if left_clean:
                        localized_left = self._localize_info_key(left_clean)
                        lines.append(f"{localized_left}{separator}{right.strip()}")
                        matched = True
                        break

            if not matched:
                lines.append(line)

        return "\n".join(lines)

    def _load_info_file(self, cfg_path: Path) -> None:
        self.current_info_path = None
        self.info_data = {}
        self._info_report_ready = False
        self.save_info_btn.setEnabled(False)
        self.info_text.clear()
        info_path = self._find_related_info_file(cfg_path)
        if info_path is None:
            self.info_text.setPlainText(self.tr("info_not_found"))
            return
        try:
            text, _ = self._read_text_with_fallback(str(info_path))
            text = text.strip()
            if not text:
                self.info_text.setPlainText(self.tr("info_empty"))
                return
            self.current_info_path = info_path
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                self.info_text.setPlainText(self._localize_info_text(text))
            else:
                if isinstance(data, dict):
                    self.info_data = data
                    self._refresh_info_report()
                else:
                    self.info_text.setPlainText(text)
            self._info_report_ready = True
            self.save_info_btn.setEnabled(True)
        except Exception as exc:
            traceback.print_exc()
            self.info_text.setPlainText(self.tr("info_read_error", error=str(exc)))

    def _analog_count(self) -> int:
        return int(self._get_attr("analog_count", "analogcount", default=0) or 0)

    def _status_count(self) -> int:
        return int(self._get_attr("status_count", "statuscount", default=0) or 0)

    def _names_analog(self) -> list[str]:
        names = self._get_attr("analog_channel_ids", "names_analog", "analog_channel_names", default=[])
        return list(names) if names is not None else []

    def _names_status(self) -> list[str]:
        names = self._get_attr("status_channel_ids", "names_status", "status_channel_names", default=[])
        return list(names) if names is not None else []

    def _station_name(self) -> str:
        return str(self._get_attr("station_name", "stationname", default="Unknown"))

    def _device_name(self) -> str:
        return str(self._get_attr("rec_dev_id", "recdevid", default="Unknown"))

    def _frequency(self) -> Any:
        return self._get_attr("frequency", default="Unknown")

    def _time_seconds(self) -> np.ndarray:
        return np.asarray(self._get_attr("time", default=[]), dtype=float)

    def _time_ms(self) -> np.ndarray:
        return self._time_seconds() * 1000.0

    def _analog_values(self, index: int) -> np.ndarray:
        return np.asarray(self._get_attr("analog", default=[])[index], dtype=float)

    def _status_values(self, index: int) -> np.ndarray:
        return np.asarray(self._get_attr("status", default=[])[index], dtype=float)

    def _channel_name(self, channel_type: str, channel_index: int) -> str:
        if channel_type == "analog":
            names = self._names_analog()
            return names[channel_index] if channel_index < len(names) else f"Analog_{channel_index}"
        names = self._names_status()
        return names[channel_index] if channel_index < len(names) else f"Status_{channel_index}"

    def _channel_values(self, channel_type: str, channel_index: int) -> np.ndarray:
        return self._analog_values(channel_index) if channel_type == "analog" else self._status_values(channel_index)

    def _channel_unit(self, channel_type: str, channel_index: int) -> str:
        if channel_type != "analog" or self.comtrade_file is None:
            return ""
        try:
            cfg = getattr(self.comtrade_file, "cfg", None)
            if cfg is not None:
                ch_list = getattr(cfg, "analog_channels", None)
                if ch_list and channel_index < len(ch_list):
                    uu = str(getattr(ch_list[channel_index], "uu", "") or "").strip()
                    if uu:
                        return uu
            cfg_text, _ = self._read_text_with_fallback(str(self.current_cfg_path))
            lines = cfg_text.splitlines()
            analog_line_idx = 2 + channel_index
            if analog_line_idx < len(lines):
                parts = lines[analog_line_idx].split(",")
                if len(parts) >= 5:
                    return parts[4].strip()
        except Exception:
            pass
        return ""

    def _detect_unit_type(self, channel_type: str, channel_index: int) -> str:
        unit = self._channel_unit(channel_type, channel_index)
        unit_lower = unit.lower().strip()
        if unit_lower in {"mm", "мм", "millimeter", "millimetre"}:
            return "mm"
        if unit_lower in {"°", "deg", "degree", "degrees", "grad", "grads",
                          "градус", "град", "угол", "angle"} or unit == "°":
            return "angle"
        if unit_lower:
            return "unknown"
        name = self._channel_name(channel_type, channel_index).lower()
        if any(k in name for k in ("мм", "mm", "ход", "travel", "stroke")):
            return "mm"
        if any(k in name for k in ("deg", "°", "угол", "grad", "angle", "поворот")):
            return "angle"
        return "unknown"

    def _get_segment_units(self) -> tuple[str, str]:
        if self.cursor1.curve is None:
            return "", ""
        unit_type = self._detect_unit_type(
            self.cursor1.curve.channel_type,
            self.cursor1.curve.channel_index,
        )
        if unit_type == "mm":
            return "мм", "м/с"
        if unit_type == "angle":
            return "°", "°/с"
        curve = self.cursor1.curve
        raw = self._channel_unit(curve.channel_type, curve.channel_index)
        return raw, f"{raw}/с" if raw else "ед./с"

    def _speed_value_for_display(self, stats: dict) -> float:
        c = self.cursor1.curve
        mm = c is not None and self._detect_unit_type(c.channel_type, c.channel_index) == "mm"
        return float(stats["avg_rate_per_ms"] if mm else stats["avg_rate_per_s"])




    def _read_text_with_fallback(self, file_path: str) -> tuple[str, str]:
        encodings_to_try = ["utf-8-sig", "cp1251", "cp866", "latin-1"]
        errors: list[str] = []

        for enc in encodings_to_try:
            try:
                text = Path(file_path).read_text(encoding=enc)
                return text, enc
            except Exception as exc:
                errors.append(f"{enc}: {exc}")

        raise RuntimeError(
            "Не удалось прочитать CFG ни в одной кодировке:\n\n" + "\n".join(errors)
        )

    def _normalize_cfg_date(self, date_str: str) -> str:
        s = date_str.strip()

        for pattern in (
            r"(\d{4})/(\d{2})/(\d{2})",
            r"(\d{4})-(\d{2})-(\d{2})",
            r"(\d{4})(\d{2})(\d{2})",
        ):
            match = re.fullmatch(pattern, s)
            if match:
                yyyy, mm, dd = match.groups()
                return f"{dd}/{mm}/{yyyy}"

        return s

    def _normalize_cfg_timestamp_line(self, line: str) -> str:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return line

        new_date = self._normalize_cfg_date(parts[0])
        time_part = ",".join(parts[1:]).strip()
        return f"{new_date},{time_part}"

    def _detect_last_sample_from_dat(self, dat_file: str) -> int:
        # Расширяем хвост до полной последней строки; обрезанную строку не принимаем.
        with open(dat_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            window = 8192
            while True:
                start = max(0, end - window)
                f.seek(start)
                raw = f.read(end - start)
                if start:
                    newline = raw.find(b"\n")
                    raw = raw[newline + 1:] if newline >= 0 else b""
                raw = raw.rstrip(b"\x1a \r\n\t")
                rows = [row.strip() for row in raw.splitlines() if row.strip()]
                if rows:
                    fields = rows[-1].decode("ascii", errors="strict").split(",")
                    if len(fields) < 2 or not fields[0].strip().isdigit():
                        raise ValueError("Последняя строка ASCII DAT повреждена")
                    return int(fields[0].strip())
                if start == 0:
                    raise ValueError("ASCII DAT пуст или не содержит отсчётов")
                window *= 2

    def _fix_cfg_text(self, text: str, last_sample: int) -> str:
        lines = text.splitlines()
        if len(lines) < 3:
            return text

        fixed = lines[:]

        for i, line in enumerate(fixed):
            stripped = line.strip()
            if "," not in stripped:
                continue
            first = stripped.split(",", 1)[0].strip()
            if (
                re.fullmatch(r"\d{4}/\d{2}/\d{2}", first)
                or re.fullmatch(r"\d{4}-\d{2}-\d{2}", first)
                or re.fullmatch(r"\d{8}", first)
            ):
                fixed[i] = self._normalize_cfg_timestamp_line(line)

        second = [p.strip() for p in fixed[1].split(",")]
        if len(second) >= 3:
            analog_count = int(re.sub(r"\D", "", second[1]) or "0")
            status_count = int(re.sub(r"\D", "", second[2]) or "0")
            idx_freq = 2 + analog_count + status_count
            idx_nrates = idx_freq + 1

            if idx_nrates < len(fixed):
                try:
                    nrates = int(fixed[idx_nrates].strip())
                except Exception:
                    nrates = 0

                idx_rates_start = idx_nrates + 1
                if nrates > 0 and idx_rates_start + nrates <= len(fixed):
                    i = idx_rates_start + nrates - 1
                    parts = [p.strip() for p in fixed[i].split(",")]
                    if len(parts) >= 2 and parts[0]:
                        fixed[i] = f"{parts[0]},{last_sample}"


        return "\n".join(fixed) + "\n"

    def _prepare_fixed_cfg_file(self, cfg_file: str, dat_file: str) -> tuple[str, str, int]:
        text, used_encoding = self._read_text_with_fallback(cfg_file)
        formats = {line.strip().upper() for line in text.splitlines()}
        if formats.intersection({"BINARY", "BINARY32", "FLOAT32"}):
            last_sample = -1
            fixed_text = text
        else:
            last_sample = self._detect_last_sample_from_dat(dat_file)
            fixed_text = self._fix_cfg_text(text, last_sample)

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding=used_encoding,
            suffix=".cfg",
            delete=False,
        )
        try:
            tmp.write(fixed_text)
            tmp.flush()
            return tmp.name, used_encoding, last_sample
        finally:
            tmp.close()

    def _load_comtrade_with_fallback(self, cfg_file: str, dat_file: str) -> tuple[Comtrade, str, int]:
        fixed_cfg_file, used_encoding, last_sample = self._prepare_fixed_cfg_file(cfg_file, dat_file)
        errors: list[str] = []
        tried: set[str] = set()

        try:
            for enc in [used_encoding, "utf-8-sig", "cp1251", "cp866", "latin-1"]:
                if enc in tried:
                    continue
                tried.add(enc)
                try:
                    rec = Comtrade()
                    rec.load(fixed_cfg_file, dat_file=dat_file, encoding=enc)
                    return rec, enc, last_sample
                except Exception as exc:
                    errors.append(f"{enc}: {exc}")

        finally:
            Path(fixed_cfg_file).unlink(missing_ok=True)

        raise RuntimeError(
            "Не удалось загрузить COMTRADE даже после исправления CFG:\n\n" + "\n".join(errors)
        )

    def open_file(self) -> None:
        cfg_file, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("select_cfg_file"),
            "",
            self.tr("cfg_filter"),
        )
        if not cfg_file:
            return

        cfg_path = Path(cfg_file)
        dat_path = next((p for p in cfg_path.parent.iterdir() if p.is_file() and p.stem == cfg_path.stem and p.suffix.lower() == ".dat"), cfg_path.with_suffix(".dat"))
        if not dat_path.exists():
            QMessageBox.critical(self, "Ошибка", f"DAT файл не найден:\n{dat_path}")
            return

        try:
            rec, used_encoding, last_sample = self._load_comtrade_with_fallback(
                str(cfg_path),
                str(dat_path),
            )
            time_data = getattr(rec, "time", None)
            time_len = 0 if time_data is None else len(time_data)
            if time_len == 0:
                raise RuntimeError(
                    "COMTRADE загружен без отсчетов: len(time)=0.\n"
                    "Проверьте соответствие CFG/DAT и блок частоты дискретизации.\n"
                    f"Последний sample из DAT: {last_sample}"
                )
            self._last_selected_channels = [
                item.data(Qt.ItemDataRole.UserRole)
                for item in self.channel_list.selectedItems()
            ]
            self.comtrade_file = rec
            self.current_cfg_path = cfg_path
            self._load_info_file(cfg_path)
            self.update_info()
            self.update_channel_list()     
            self.clear_plot(keep_message=True)
            self._try_restore_and_plot()
            self.statusBar().showMessage(
                self.tr("loaded_status", name=self.current_cfg_path.name, encoding=used_encoding, samples=time_len),
                7000,
            )
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{exc}")

    def update_info(self) -> None:
        # Формирование сводной информации о COMTRADE-файле для левой панели.
        if self.comtrade_file is None:
            self.info_label.setText(self.tr("file_not_loaded"))
            return
        time_ms = self._time_ms()
        duration_ms = float(time_ms[-1] - time_ms[0]) if time_ms.size else 0.0
        source = self.current_cfg_path.name if self.current_cfg_path else "Unknown"
        info = (
            f"≡ Файл: {source}\n"
            f"Устройство: {self._device_name()}\n\n"
            f"Аналоговые каналы: {self._analog_count()}\n"
            f"Дискретные каналы: {self._status_count()}\n"
            f"Длительность измерения: {duration_ms:.1f} мс"
        )
        self.info_label.setText(info)

    def update_channel_list(self) -> None:
        # Перезаполнение списка каналов после загрузки нового файла.
        self.channel_list.clear()
        if self.comtrade_file is None:
            return

        analog_names = self._names_analog()
        status_names = self._names_status()

        for i in range(self._analog_count()):
            name = analog_names[i] if i < len(analog_names) else f"Analog_{i}"
            item = QListWidgetItem(f"[A{i}] {name}")
            item.setData(Qt.ItemDataRole.UserRole, ("analog", i))
            self.channel_list.addItem(item)

        for i in range(self._status_count()):
            name = status_names[i] if i < len(status_names) else f"Status_{i}"
            item = QListWidgetItem(f"[S{i}] {name}")
            item.setData(Qt.ItemDataRole.UserRole, ("status", i))
            self.channel_list.addItem(item)
    def _restore_channel_selection(self, channels: list[tuple[str, int]]) -> int:
        """Восстанавливает выделение элементов в channel_list по сохранённому набору."""
        if not channels:
            return 0
        saved_set = set(channels)
        restored_count = 0
        for i in range(self.channel_list.count()):
            item = self.channel_list.item(i)
            if item is None:
                continue
            if item.data(Qt.ItemDataRole.UserRole) in saved_set:
                item.setSelected(True)
                restored_count += 1
        return restored_count

    def _try_restore_and_plot(self) -> None:
        """
        Пытается восстановить выбор каналов из предыдущего измерения.
        Если хотя бы один канал совпал — строит график автоматически.
        """
        if not self._last_selected_channels:
            return

        restored = self._restore_channel_selection(self._last_selected_channels)
        if not restored:
            return  # Нет совпадений — ничего не строим

        # Каналы совпали — строим сразу
        items = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.channel_list.selectedItems()
        ]
        if not items:
            return

        try:
            if self.plot_mode == "overlay":
                self._plot_overlay(items)
            else:
                self._plot_parallel(items)
            self._refresh_measurement_curve_combo()
            self._redraw_cursors()
            self.statusBar().showMessage(
                f"Восстановлены каналы с предыдущего измерения: {len(items)} шт.", 5000
            )
        except Exception as exc:
            traceback.print_exc()

    def on_mode_changed(self) -> None:
        self.plot_mode = str(self.mode_combo.currentData())

    def clear_plot(self, keep_message: bool = False) -> None:
        # Полная очистка графической области.
        # Одновременно сбрасываются построенные кривые, курсоры и текстовые аннотации.
        self.canvas.fig.clear()
        self.curves.clear()
        self._clear_annotation_artists()
        self.clear_cursors(redraw=False)
        self._refresh_measurement_curve_combo()

        ax = self.canvas.fig.add_subplot(111)
        ax.grid(True, alpha=0.3)
        ax.set_title(self.tr("load_and_select"))
        ax.set_xlabel(self.tr("time_axis"))
        ax.set_ylabel(self.tr("value_axis"))
        self.canvas.draw_idle()
        if not keep_message:
            self.statusBar().clearMessage()

    def plot_selected_channels(self) -> None:
        # Построение только тех каналов, которые пользователь выделил в списке.
        if self.comtrade_file is None:
            QMessageBox.warning(self, self.tr("error"), self.tr("warn_load_first"))
            return

        selected = self.channel_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, self.tr("error"), self.tr("warn_select_channel"))
            return

        items = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        try:
            if self.plot_mode == "overlay":
                self._plot_overlay(items)
            else:
                self._plot_parallel(items)
            self._refresh_measurement_curve_combo()
            self._redraw_cursors()
            self.statusBar().showMessage(self.tr("plot_done"), 3000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Ошибка построения графика:\n{exc}")

    def plot_all_analog(self) -> None:
        # Удобный режим для быстрого просмотра всех аналоговых каналов файла.
        if self.comtrade_file is None:
            QMessageBox.warning(self, self.tr("error"), self.tr("warn_load_first"))
            return
        if self._analog_count() == 0:
            QMessageBox.warning(self, self.tr("error"), self.tr("warn_no_analog"))
            return

        items = [("analog", i) for i in range(self._analog_count())]
        try:
            self._plot_parallel(items, title=f"{self.tr('plot_all_analog')} — {self._station_name()}")
            self._refresh_measurement_curve_combo()
            self._redraw_cursors()
            self.statusBar().showMessage(self.tr("plot_all_done"), 3000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Ошибка построения графика:\n{exc}")

    def _plot_overlay(self, items: list[tuple[str, int]]) -> None:
        # Все выбранные сигналы строятся на одной общей оси.
        # Такой режим удобен для визуального сравнения формы сигналов.
        self.canvas.fig.clear()
        self.curves.clear()
        self._clear_annotation_artists()
        self.clear_cursors(redraw=False)

        ax = self.canvas.fig.add_subplot(111)
        time_ms = self._time_ms()
        colors = ["tab:blue", "tab:red", "tab:green", "tab:orange", "tab:purple", "tab:brown", "tab:pink", "tab:cyan"]

        for i, (channel_type, channel_index) in enumerate(items):
            values = self._channel_values(channel_type, channel_index)
            label = self._channel_name(channel_type, channel_index)
            line, = ax.plot(time_ms, values, linewidth=1.4, color=colors[i % len(colors)], label=label)
            uid = f"{channel_type}:{channel_index}"
            self.curves.append(CurveRef(uid=uid, ax=ax, line=line, channel_type=channel_type, channel_index=channel_index, label=label, time_ms=time_ms, values=values))

        ax.set_title(self.tr("overlay_title", station=self._station_name()))
        ax.set_xlabel(self.tr("time_ms_axis"))
        ax.set_ylabel(self.tr("value_axis"))
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()

    def _plot_parallel(self, items: list[tuple[str, int]], title: Optional[str] = None) -> None:
        # Каждый канал строится на своей оси, но по общей временной шкале.
        # Это упрощает анализ при большом количестве кривых с разным диапазоном значений.
        self.canvas.fig.clear()
        self.curves.clear()
        self._clear_annotation_artists()
        self.clear_cursors(redraw=False)

        n = len(items)
        time_ms = self._time_ms()
        shared_ax: Optional[Axes] = None

        for row, (channel_type, channel_index) in enumerate(items, start=1):
            ax = self.canvas.fig.add_subplot(n, 1, row, sharex=shared_ax)
            if shared_ax is None:
                shared_ax = ax

            values = self._channel_values(channel_type, channel_index)
            label = self._channel_name(channel_type, channel_index)
            unit  = self._channel_unit(channel_type, channel_index)
            color = "tab:blue" if channel_type == "analog" else "tab:red"
            line, = ax.plot(time_ms, values, linewidth=1.2, color=color)
            ylabel = f"{label}, {unit}" if unit else label
            ax.set_ylabel(ylabel, fontsize=8)
            ax.grid(True, alpha=0.3)
            if row < n:
                ax.tick_params(labelbottom=False)
            else:
                ax.set_xlabel(self.tr("time_ms_axis"))

            uid = f"{channel_type}:{channel_index}"
            self.curves.append(CurveRef(uid=uid, ax=ax, line=line, channel_type=channel_type, channel_index=channel_index, label=label, time_ms=time_ms, values=values))

        self.canvas.fig.suptitle(title or self.tr("parallel_title", station=self._station_name()))
        self.canvas.draw_idle()

    def _refresh_measurement_curve_combo(self) -> None:
        # Выпадающий список кривых для режима измерения "Только одна выбранная кривая".
        # Он обновляется после каждой новой перерисовки графика.
        previous_uid = self.measure_curve_combo.currentData()
        self.measure_curve_combo.clear()
        if not self.curves:
            self.measure_curve_combo.addItem(self.tr("no_curves"), None)
            return

        for curve in self.curves:
            title = f"{curve.label} [{curve.uid}]"
            self.measure_curve_combo.addItem(title, curve.uid)

        if previous_uid is not None:
            idx = self.measure_curve_combo.findData(previous_uid)
            if idx >= 0:
                self.measure_curve_combo.setCurrentIndex(idx)

    def _curve_by_uid(self, uid: Optional[str]) -> Optional[CurveRef]:
        if uid is None:
            return None
        for curve in self.curves:
            if curve.uid == uid:
                return curve
        return None

    def _selected_measurement_curve(self) -> Optional[CurveRef]:
        return self._curve_by_uid(self.measure_curve_combo.currentData())

    def clear_cursors(self, redraw: bool = True) -> None:
        # Сброс обоих курсоров и очистка всех вычисленных аннотаций на графике.
        self.cursor1.clear()
        self.cursor2.clear()
        self._clear_annotation_artists()
        self._update_measurement_info()
        if redraw:
            self.canvas.draw_idle()

    def _clear_annotation_artists(self) -> None:
        for artist in self.annotation_artists:
            try:
                artist.remove()
            except Exception:
                pass
        self.annotation_artists.clear()

    def on_canvas_click(self, event: Any) -> None:
        # Основная логика работы курсоров.
        # ЛКМ управляет первым курсором, ПКМ — вторым.
        # Если клик произошел рядом с существующей линией курсора, курсор удаляется.
        # Иначе курсор ставится на ближайший отсчет выбранной кривой.
        if self.comtrade_file is None or event.inaxes is None:
            return
        if event.button not in (1, 3):
            return
        if self.toolbar.mode:
            return

        target = self.cursor1 if event.button == 1 else self.cursor2
        if self._click_near_cursor_line(event, target):
            target.clear()
            self._clear_annotation_artists()
            self._redraw_cursors()
            self._update_measurement_info()
            self.canvas.draw_idle()
            return

        curve, sample_index = self._pick_curve_and_index(event)
        if curve is None or sample_index is None:
            self.statusBar().showMessage(self.tr("measure_pick_failed"), 3000)
            return

        self._set_cursor(target, curve, sample_index)
        self._redraw_cursors()
        self._update_measurement_info()
        self.canvas.draw_idle()

    def _set_cursor(self, cursor: CursorState, curve: CurveRef, sample_index: int) -> None:
        # Привязка курсора к конкретной кривой и конкретному отсчету этой кривой.
        idx = int(sample_index)
        cursor.ax = curve.ax
        cursor.curve = curve
        cursor.sample_index = idx
        cursor.x_ms = float(curve.time_ms[idx])
        cursor.y_value = float(curve.values[idx])

    def _pick_curve_and_index(self, event: Any) -> tuple[Optional[CurveRef], Optional[int]]:
        # Определение, к какой кривой относится текущий клик.
        # В single-режиме выбирается только заранее указанная кривая.
        # В auto-режиме оценивается близость клика к нескольким кривым на активной оси.
        if event.xdata is None:
            return None, None

        mode = self.measure_mode_combo.currentData()
        if mode == "single":
            selected_curve = self._selected_measurement_curve()
            if selected_curve is None:
                return None, None
            if event.inaxes is not selected_curve.ax:
                return None, None
            idx = int(np.argmin(np.abs(selected_curve.time_ms - float(event.xdata))))
            return selected_curve, idx

        # Предпочитаем кривые на кликнутой оси; если их нет — ищем среди всех
        candidates_on_ax = [curve for curve in self.curves if curve.ax is event.inaxes]
        candidates = candidates_on_ax if candidates_on_ax else list(self.curves)
        if not candidates:
            return None, None

        x_click = float(event.xdata)
        best_curve: Optional[CurveRef] = None
        best_idx: Optional[int] = None
        best_dist = float("inf")

        for curve in candidates:
            idx = int(np.argmin(np.abs(curve.time_ms - x_click)))
            dist = abs(float(curve.time_ms[idx]) - x_click)
            if dist < best_dist:
                best_dist = dist
                best_curve = curve
                best_idx = idx

        return best_curve, best_idx

    def _click_near_cursor_line(self, event: Any, cursor: CursorState, threshold_px: float = 8.0) -> bool:
        # Линия курсора проходит по всем осям — проверяем попадание
        # через ту ось, на которой произошёл клик.
        if not cursor.is_set or cursor.ax is None or cursor.x_ms is None:
            return False
        if event.inaxes is None or event.x is None:
            return False
        ax = event.inaxes
        y_mid = float(np.mean(ax.get_ylim()))
        x_disp, _ = ax.transData.transform((cursor.x_ms, y_mid))
        return abs(float(event.x) - float(x_disp)) <= threshold_px

    def _redraw_cursors(self) -> None:
        self.cursor1.remove_artists()
        self.cursor2.remove_artists()
        self._clear_annotation_artists()

        # self.canvas.fig — Figure, хранящаяся в ComtradePlotCanvas
        all_axes = self.canvas.fig.get_axes()

        for cursor in (self.cursor1, self.cursor2):
            if not cursor.is_set:
                continue
            assert cursor.ax is not None and cursor.x_ms is not None and cursor.y_value is not None
            # Вертикальная линия — на ВСЕХ осях
            for ax in all_axes:
                vline = ax.axvline(
                    cursor.x_ms, color=cursor.color,
                    linestyle="--", linewidth=1.2, alpha=0.7,
                )
                cursor.artists.append(vline)
            # Маркер и подпись — только на оси привязки
            marker, = cursor.ax.plot(
                cursor.x_ms, cursor.y_value,
                marker="o", markersize=7, color=cursor.color,
            )
            text = cursor.ax.annotate(
                f"{cursor.name}\n{cursor.x_ms:.3f} мс\n{cursor.y_value:.2f}",
                xy=(cursor.x_ms, cursor.y_value),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=8,
                bbox=dict(boxstyle="round", fc="white", ec=cursor.color, alpha=0.9),
            )
            cursor.artists.extend([marker, text])

        segment_text = self._build_segment_annotation()
        if segment_text and self.cursor1.is_set and self.cursor1.ax is not None:
            ann = self.cursor1.ax.text(
                0.015,
                0.985,
                segment_text,
                transform=self.cursor1.ax.transAxes,
                va="top",
                ha="left",
                fontsize=8.5,
                family="monospace",
                bbox=dict(boxstyle="round", fc="lightyellow", ec="black", alpha=0.92),
            )
            self.annotation_artists.append(ann)

    def _build_segment_annotation(self) -> str:
        stats = self._calculate_segment_stats()
        if stats is None:
            return ""
        val_unit, speed_unit = self._get_segment_units()
        speed_val = self._speed_value_for_display(stats)
        lines = [
            f"DT = {stats['delta_t_ms']:.2f} мс",
            f"DY = {stats['delta_y']:.2f} {val_unit}",
            f"Vsr = {speed_val:.2f} {speed_unit}",
        ]
        return "\n".join(lines)

    def _calculate_segment_stats(self) -> Optional[dict[str, Any]]:
        # Расчет всех параметров участка между двумя курсорами.
        # Важное ограничение: оба курсора должны быть поставлены на одну и ту же кривую,
        # иначе математически корректный расчет по участку невозможен.
        if not (self.cursor1.is_set and self.cursor2.is_set):
            return None
        if self.cursor1.curve is None or self.cursor2.curve is None:
            return None
        if self.cursor1.curve is not self.cursor2.curve:
            return None
        if self.cursor1.sample_index is None or self.cursor2.sample_index is None:
            return None

        curve = self.cursor1.curve
        i1 = int(self.cursor1.sample_index)
        i2 = int(self.cursor2.sample_index)
        # Участок всегда обрабатывается слева направо,
        # даже если пользователь поставил курсоры в обратном порядке.
        lo, hi = sorted((i1, i2))

        x1 = float(curve.time_ms[i1])
        x2 = float(curve.time_ms[i2])
        y1 = float(curve.values[i1])
        y2 = float(curve.values[i2])
        delta_t_ms = x2 - x1
        delta_y = y2 - y1
        if abs(delta_t_ms) < 1e-15:
            return None

        t_seg_ms = np.asarray(curve.time_ms[lo:hi + 1], dtype=float)
        y_seg = np.asarray(curve.values[lo:hi + 1], dtype=float)
        # Локальные производные считаются между соседними отсчетами участка.
        dt_ms = np.diff(t_seg_ms)
        dy = np.diff(y_seg)
        valid = np.abs(dt_ms) > 0

        local_rates_per_ms = np.array([], dtype=float)
        local_rates_per_s = np.array([], dtype=float)
        max_pair = None

        if np.any(valid):
            local_rates_per_ms = dy[valid] / dt_ms[valid]
            local_rates_per_s = dy[valid] / (dt_ms[valid] / 1000.0)

            valid_indices = np.nonzero(valid)[0]
            local_abs = np.abs(local_rates_per_s)
            max_local_idx = int(np.argmax(local_abs))
            pair_start = int(valid_indices[max_local_idx]) + lo
            pair_end = pair_start + 1
            max_pair = {
                "i_start": pair_start,
                "i_end": pair_end,
                "t_start_ms": float(curve.time_ms[pair_start]),
                "t_end_ms": float(curve.time_ms[pair_end]),
                "y_start": float(curve.values[pair_start]),
                "y_end": float(curve.values[pair_end]),
                "rate_per_s": float(local_rates_per_s[max_local_idx]),
                "abs_rate_per_s": float(local_abs[max_local_idx]),
            }

        stats: dict[str, Any] = {
            "curve_uid": curve.uid,
            "curve_label": curve.label,
            "i1": i1,
            "i2": i2,
            "lo": lo,
            "hi": hi,
            "x1_ms": x1,
            "x2_ms": x2,
            "y1": y1,
            "y2": y2,
            "delta_t_ms": delta_t_ms,
            "delta_y": delta_y,
            "avg_rate_per_ms": delta_y / delta_t_ms,
            "avg_rate_per_s": delta_y / (delta_t_ms / 1000.0),
            "y_min": float(np.min(y_seg)),
            "y_max": float(np.max(y_seg)),
            "samples": int(len(y_seg)),
            "segment_time_ms": t_seg_ms,
            "segment_values": y_seg,
        }
        if local_rates_per_ms.size:
            stats.update(
                {
                    "local_rate_min_per_ms": float(np.min(local_rates_per_ms)),
                    "local_rate_max_per_ms": float(np.max(local_rates_per_ms)),
                    "local_rate_abs_max_per_ms": float(np.max(np.abs(local_rates_per_ms))),
                    "local_rate_min_per_s": float(np.min(local_rates_per_s)),
                    "local_rate_max_per_s": float(np.max(local_rates_per_s)),
                    "local_rate_abs_max_per_s": float(np.max(np.abs(local_rates_per_s))),
                    "segment_dt_ms": dt_ms,
                    "segment_dy": dy,
                    "segment_local_rates_per_s": local_rates_per_s,
                    "segment_local_rates_per_ms": local_rates_per_ms,
                    "max_pair": max_pair,
                }
            )
        return stats

    def _cursor_block(self, cursor: CursorState, icon: str) -> str:
        # Формирование текстового блока по одному курсору для панели измерений.
        if not cursor.is_set or cursor.curve is None or cursor.sample_index is None:
            return f"{icon} {cursor.name}: не установлен"
        return (
            f"{icon} {cursor.name}\n"
            f"  Канал: {cursor.curve.label}\n"
            f"  T: {cursor.x_ms:.2f} мс\n"
            f"  Y: {cursor.y_value:.2f}"
        )

    def _update_measurement_info(self) -> None:
        # Обновление подробного текстового отчета в нижней левой панели.
        # Здесь показываются состояние курсоров, режим привязки и параметры участка.
        parts = [
            "ИЗМЕРЕНИЯ КУРСОРОВ\n",
            self._cursor_block(self.cursor1, "🔴"),
            self._cursor_block(self.cursor2, "🔵"),
        ]

        mode = self.measure_mode_combo.currentData()
        selected_curve = self._selected_measurement_curve()
        selected_text = selected_curve.label if selected_curve is not None else "не выбрана"
        parts.append(
            "Режим привязки\n"
            f"  Режим: {'Авто' if mode == 'auto' else 'Только одна кривая'}\n"
            f"  Выбранная кривая: {selected_text}"
        )

        stats = self._calculate_segment_stats()
        if stats is None:
            if self.cursor1.is_set and self.cursor2.is_set:
                parts.append(
                    "Участок между курсорами\n"
                    "  Для расчёта оба курсора должны быть на одной и той же кривой."
                )
        else:
            val_unit, speed_unit = self._get_segment_units()
            speed_val = self._speed_value_for_display(stats)
            block = (
                "Участок между курсорами\n"
                f"  Канал: {stats['curve_label']}\n"
                f"  DT: {stats['delta_t_ms']:.2f} мс\n"
                f"  DY: {stats['delta_y']:.2f} {val_unit}\n"
                f"  Vср: {speed_val:.2f} {speed_unit}\n"
            )
            parts.append(block)
            
        self.cursor_info.setPlainText("\n\n".join(parts))


    def _refresh_info_report(self) -> None:
        data = self.info_data
        path = str(self.current_info_path or "")
        if str(data.get("type") or "").lower() == "drm" or "microohm_results" in data:
            report = convert_drm_json_to_report(data, source_path=path)
            report += "\n\n" + format_info_metadata(data, self.breaker_database)
        elif "meas_sett" in data or "calc_data" in data:
            report = convert_info_json_to_report(data, source_path=path, breaker_database=self.breaker_database)
        else:
            report = format_info_metadata(data, self.breaker_database) + "\n\n" + json.dumps(data, ensure_ascii=False, indent=2)
        self.info_text.setPlainText(report)

    def edit_breaker_record(self) -> None:
        if self._db_error:
            QMessageBox.warning(self, "База выключателей", self._db_error + "\nИсправьте базу и перезапустите программу.")
            return
        raw = get_info_metadata(self.info_data)["table_uuid"]
        try:
            key = normalize_uuid(raw)
            if not key:
                raise ValueError("В текущем INFO отсутствует UUID")
        except ValueError as exc:
            QMessageBox.warning(self, "UUID", f"Некорректный или отсутствующий UUID: {raw}\n{exc}")
            return
        old = get_breaker_record(self.breaker_database, key)
        dialog = BreakerEditDialog(self, key, old)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = copy.deepcopy(self.breaker_database)
        if dialog.action == "delete":
            updated["breakers"].pop(key, None)
        elif dialog.action == "save":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            updated["breakers"][key] = dict(uuid=key, name=dialog.name_edit.text().strip(),
                comment=dialog.comment_edit.toPlainText().strip(), voltage_kv=dialog.voltage,
                created=old.get("created") or now, updated=now)
        else:
            return
        try:
            save_breaker_database(updated)
        except Exception as exc:
            QMessageBox.critical(self, "База выключателей", f"Не удалось сохранить базу:\n{exc}")
            return
        self.breaker_database = updated
        self._refresh_info_report()
        self.statusBar().showMessage("База выключателей обновлена", 5000)

    def export_measurements_csv(self) -> None:
        if self.comtrade_file is None:
            QMessageBox.warning(self, self.tr("error"), self.tr("warn_load_first"))
            return
        default = f"{self.current_cfg_path.stem}_measurements.csv" if self.current_cfg_path else "measurements.csv"
        filename, _ = QFileDialog.getSaveFileName(self, self.tr("save_csv"), str(Path.home() / default), self.tr("csv_filter"))
        if not filename:
            return
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["section", "key", "value", "unit"])
                def row(section, key, value, unit=""):
                    if value is None:
                        value = ""
                    elif isinstance(value, bool):
                        value = int(value)
                    elif isinstance(value, (int, float)):
                        value = format(value, ".12g").replace(".", ",")
                    writer.writerow([section, key, value, unit])
                row("file", "cfg_name", self.current_cfg_path.name if self.current_cfg_path else "")
                row("file", "device", self._device_name())
                if self.info_data:
                    meta = get_info_metadata(self.info_data)
                    row("info", "source", str(self.current_info_path or ""))
                    for key in ("device", "serial", "ver"):
                        row("device_info", key, meta[key])
                    for key in ("table_uuid", "initial_value", "unit_out_str", "recalc_present", "value_unit", "speed_unit", "unit_source"):
                        row("recalc_settings", key, meta[key])
                    record = get_breaker_record(self.breaker_database, meta["table_uuid"])
                    for key in ("name", "voltage_kv", "comment"):
                        row("breaker", key, record.get(key), "кВ" if key == "voltage_kv" else "")
                    cd = self.info_data.get("calc_data") or {}
                    for i, op in enumerate(cd.get("operations") or [], 1):
                        if not isinstance(op, dict) or op.get("p_switch_dur") is None:
                            continue
                        section = f"info_operation_{i}"
                        for key, value in op.items():
                            if key == "poles":
                                continue
                            unit = ""
                            if key in {"p_switch_dur", "p_max_desync"} and isinstance(value, (int, float)):
                                value, unit = value / 1000, "мс"
                            elif key == "s_avg_speed":
                                unit = meta["speed_unit"]
                            elif key.startswith("s_"):
                                unit = meta["value_unit"]
                            row(section, key, value, unit)
                        for j, pole in enumerate(op.get("poles") or [], 1):
                            for key, value in (pole or {}).items():
                                unit = ""
                                if key in {"p_switch_dur", "p_desync", "p_vibr"} and isinstance(value, (int, float)):
                                    value, unit = value / 1000, "мс"
                                elif key.startswith("s_"):
                                    unit = meta["value_unit"]
                                row(f"{section}_pole_{j}", key, value, unit)
                for cursor in (self.cursor1, self.cursor2):
                    row(cursor.name, "is_set", cursor.is_set)
                    if cursor.is_set:
                        row(cursor.name, "channel", cursor.curve.label)
                        row(cursor.name, "sample_index", cursor.sample_index)
                        row(cursor.name, "time_ms", cursor.x_ms, "мс")
                        row(cursor.name, "value", cursor.y_value, self._channel_unit(cursor.curve.channel_type, cursor.curve.channel_index))
                stats = self._calculate_segment_stats()
                if stats:
                    value_unit, speed_unit = self._get_segment_units()
                    for key, unit in [("delta_t_ms", "мс"), ("delta_y", value_unit), ("y_min", value_unit), ("y_max", value_unit)]:
                        row("segment", key, stats[key], unit)
                    row("segment", "average_rate", self._speed_value_for_display(stats), speed_unit)
                    writer.writerow([])
                    writer.writerow(["sample_index", "time_ms", "value", "unit"])
                    for i, (t, y) in enumerate(zip(stats["segment_time_ms"], stats["segment_values"]), stats["lo"]):
                        writer.writerow([i, format(t, ".12g").replace(".", ","), format(y, ".12g").replace(".", ","), value_unit])
            self.statusBar().showMessage(self.tr("csv_saved", path=filename), 6000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", str(exc))

    def save_info_report(self) -> None:
        """Сохраняет текущее содержимое вкладки 'Результаты' в текстовый файл."""
        if not self._info_report_ready:
            QMessageBox.warning(self, self.tr("error"), self.tr("info_no_file"))
            return
        report_text = self.info_text.toPlainText().strip()
        if not report_text or report_text in (
            self.tr("info_not_found"),
            self.tr("info_empty"),
        ):
            QMessageBox.warning(self, self.tr("error"), self.tr("info_no_file"))
            return

        default_name = "info_report.txt"
        if self.current_info_path is not None:
            default_name = self.current_info_path.stem + "_report.txt"
        elif self.current_cfg_path is not None:
            default_name = self.current_cfg_path.stem + "_report.txt"

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("save_info_report"),
            str(Path.home() / default_name),
            self.tr("info_report_filter"),
        )
        if not file_name:
            return

        try:
            Path(file_name).write_text(report_text, encoding="utf-8")
            self.statusBar().showMessage(self.tr("info_report_saved", path=file_name), 6000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, self.tr("error"), self.tr("info_report_save_failed", error=str(exc)))



# Точка входа в приложение.
def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("COMTRADE Viewer")
    window = ComtradeViewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

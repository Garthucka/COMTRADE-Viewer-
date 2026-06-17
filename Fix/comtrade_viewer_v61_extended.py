#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COMTRADE Viewer V6.1

Расширенный просмотрщик COMTRADE с исправленными курсорами, измерениями по участкам,
экспортом в CSV и гибкой компоновкой боковых панелей.

Зависимости:
    pip install comtrade PyQt6 matplotlib numpy pyinstaller
"""

from __future__ import annotations

import csv
import sys
import traceback
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from comtrade import Comtrade
except ImportError as exc:
    raise SystemExit("Не найдена библиотека 'comtrade'. Установите: pip install comtrade") from exc


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
        self.ax = None
        self.curve = None
        self.sample_index = None
        self.x_ms = None
        self.y_value = None
        self.remove_artists()

    def remove_artists(self) -> None:
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


class ComtradePlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent: Optional[QWidget] = None):
        self.fig = Figure(figsize=(12, 7), dpi=100, constrained_layout=True)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setFocus()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)


class ComtradeViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("COMTRADE Viewer V6.1")
        self.resize(1750, 980)

        self.comtrade_file: Optional[Comtrade] = None
        self.current_cfg_path: Optional[Path] = None
        self.plot_mode = "parallel"
        self.curves: list[CurveRef] = []
        self.annotation_artists: list[Artist] = []
        self.cursor1 = CursorState(name="Курсор 1", color="red")
        self.cursor2 = CursorState(name="Курсор 2", color="blue")

        self._init_ui()
        self._connect_canvas_events()
        self._update_measurement_info()
        self.clear_plot(keep_message=True)

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.main_splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_layout.addWidget(self.left_splitter)

        left_top = QWidget()
        top_layout = QVBoxLayout(left_top)

        self.open_btn = QPushButton("📂 Открыть CFG файл")
        self.open_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.open_btn.clicked.connect(self.open_file)
        top_layout.addWidget(self.open_btn)

        self.info_label = QLabel("Файл не загружен")
        self.info_label.setWordWrap(True)
        self.info_label.setFont(QFont("Arial", 9))
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top_layout.addWidget(self.info_label)

        mode_group = QGroupBox("Режим просмотра")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Наложение графиков", "overlay")
        self.mode_combo.addItem("Параллельный просмотр", "parallel")
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        top_layout.addWidget(mode_group)

        channels_group = QGroupBox("Каналы для отображения")
        channels_layout = QVBoxLayout(channels_group)
        self.channel_list = QListWidget()
        self.channel_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        channels_layout.addWidget(self.channel_list)
        top_layout.addWidget(channels_group, 1)

        plot_buttons_group = QGroupBox("Построение")
        plot_buttons_layout = QVBoxLayout(plot_buttons_group)
        self.plot_btn = QPushButton("📈 Отобразить выбранные")
        self.plot_btn.clicked.connect(self.plot_selected_channels)
        plot_buttons_layout.addWidget(self.plot_btn)
        self.plot_all_btn = QPushButton("📊 Все аналоговые")
        self.plot_all_btn.clicked.connect(self.plot_all_analog)
        plot_buttons_layout.addWidget(self.plot_all_btn)
        top_layout.addWidget(plot_buttons_group)

        measurement_group = QGroupBox("Режим измерения")
        measurement_layout = QVBoxLayout(measurement_group)
        self.measure_mode_combo = QComboBox()
        self.measure_mode_combo.addItem("Авто: ближайшая кривая на активной оси", "auto")
        self.measure_mode_combo.addItem("Только одна выбранная кривая", "single")
        measurement_layout.addWidget(self.measure_mode_combo)

        self.measure_curve_combo = QComboBox()
        self.measure_curve_combo.addItem("Нет построенных кривых", None)
        measurement_layout.addWidget(self.measure_curve_combo)
        top_layout.addWidget(measurement_group)

        actions_group = QGroupBox("Измерения и экспорт")
        actions_layout = QVBoxLayout(actions_group)
        self.export_csv_btn = QPushButton("💾 Экспорт измерений в CSV")
        self.export_csv_btn.clicked.connect(self.export_measurements_csv)
        actions_layout.addWidget(self.export_csv_btn)

        self.clear_cursor_btn = QPushButton("🗑 Очистить оба курсора")
        self.clear_cursor_btn.clicked.connect(self.clear_cursors)
        actions_layout.addWidget(self.clear_cursor_btn)
        top_layout.addWidget(actions_group)

        help_group = QGroupBox("Подсказка")
        help_layout = QVBoxLayout(help_group)
        help_label = QLabel(
            "ЛКМ: установить или удалить Курсор 1\n"
            "ПКМ: установить или удалить Курсор 2"
        )
        help_label.setWordWrap(True)
        help_layout.addWidget(help_label)
        top_layout.addWidget(help_group)

        top_layout.addStretch(1)

        left_bottom = QWidget()
        bottom_layout = QVBoxLayout(left_bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        measure_info_group = QGroupBox("Измерения")
        measure_info_layout = QVBoxLayout(measure_info_group)
        self.cursor_info = QTextEdit()
        self.cursor_info.setReadOnly(True)
        self.cursor_info.setFont(QFont("Consolas", 9))
        measure_info_layout.addWidget(self.cursor_info)
        bottom_layout.addWidget(measure_info_group)

        self.left_splitter.addWidget(left_top)
        self.left_splitter.addWidget(left_bottom)
        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 2)
        self.left_splitter.setSizes([620, 300])

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

        self.statusBar().showMessage("Готово")

    def _connect_canvas_events(self) -> None:
        self.canvas.mpl_connect("button_press_event", self.on_canvas_click)

    def _get_attr(self, *names: str, default: Any = None) -> Any:
        if self.comtrade_file is None:
            return default
        for name in names:
            if hasattr(self.comtrade_file, name):
                return getattr(self.comtrade_file, name)
        return default

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

    def open_file(self) -> None:
        cfg_file, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите COMTRADE CFG файл",
            "",
            "COMTRADE CFG (*.cfg);;Все файлы (*.*)",
        )
        if not cfg_file:
            return

        try:
            rec = Comtrade()
            rec.load(cfg_file)
            self.comtrade_file = rec
            self.current_cfg_path = Path(cfg_file)
            self.update_info()
            self.update_channel_list()
            self.clear_plot(keep_message=True)
            self.statusBar().showMessage(f"Загружен файл: {self.current_cfg_path.name}", 5000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{exc}")

    def update_info(self) -> None:
        if self.comtrade_file is None:
            self.info_label.setText("Файл не загружен")
            return
        time_ms = self._time_ms()
        duration_ms = float(time_ms[-1]) if time_ms.size else 0.0
        source = self.current_cfg_path.name if self.current_cfg_path else "Unknown"
        info = (
            f"📄 Файл: {source}\n"
            f"Станция: {self._station_name()}\n"
            f"Устройство: {self._device_name()}\n\n"
            f"Аналоговые каналы: {self._analog_count()}\n"
            f"Статусные каналы: {self._status_count()}\n"
            f"Частота: {self._frequency()} Гц\n"
            f"Отсчётов: {time_ms.size}\n"
            f"Длительность: {duration_ms:.6f} мс"
        )
        self.info_label.setText(info)

    def update_channel_list(self) -> None:
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

    def on_mode_changed(self) -> None:
        self.plot_mode = str(self.mode_combo.currentData())

    def clear_plot(self, keep_message: bool = False) -> None:
        self.canvas.fig.clear()
        self.curves.clear()
        self._clear_annotation_artists()
        self.clear_cursors(redraw=False)
        self._refresh_measurement_curve_combo()

        ax = self.canvas.fig.add_subplot(111)
        ax.grid(True, alpha=0.3)
        ax.set_title("Загрузите файл и выберите каналы")
        ax.set_xlabel("Время")
        ax.set_ylabel("Значение")
        self.canvas.draw_idle()
        if not keep_message:
            self.statusBar().clearMessage()

    def plot_selected_channels(self) -> None:
        if self.comtrade_file is None:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите COMTRADE файл.")
            return

        selected = self.channel_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Ошибка", "Выберите хотя бы один канал.")
            return

        items = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
        try:
            if self.plot_mode == "overlay":
                self._plot_overlay(items)
            else:
                self._plot_parallel(items)
            self._refresh_measurement_curve_combo()
            self._redraw_cursors()
            self.statusBar().showMessage("График построен", 3000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Ошибка построения графика:\n{exc}")

    def plot_all_analog(self) -> None:
        if self.comtrade_file is None:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите COMTRADE файл.")
            return
        if self._analog_count() == 0:
            QMessageBox.warning(self, "Ошибка", "В файле нет аналоговых каналов.")
            return

        items = [("analog", i) for i in range(self._analog_count())]
        try:
            self._plot_parallel(items, title=f"Все аналоговые каналы — {self._station_name()}")
            self._refresh_measurement_curve_combo()
            self._redraw_cursors()
            self.statusBar().showMessage("Построены все аналоговые каналы", 3000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Ошибка построения графика:\n{exc}")

    def _plot_overlay(self, items: list[tuple[str, int]]) -> None:
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

        ax.set_title(f"Наложение графиков — {self._station_name()}")
        ax.set_xlabel("Время, мс")
        ax.set_ylabel("Значение")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
        self.canvas.draw_idle()

    def _plot_parallel(self, items: list[tuple[str, int]], title: Optional[str] = None) -> None:
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
            color = "tab:blue" if channel_type == "analog" else "tab:red"
            line, = ax.plot(time_ms, values, linewidth=1.2, color=color)
            ax.set_ylabel(label)
            ax.grid(True, alpha=0.3)
            if row < n:
                ax.tick_params(labelbottom=False)
            else:
                ax.set_xlabel("Время, мс")

            uid = f"{channel_type}:{channel_index}"
            self.curves.append(CurveRef(uid=uid, ax=ax, line=line, channel_type=channel_type, channel_index=channel_index, label=label, time_ms=time_ms, values=values))

        self.canvas.fig.suptitle(title or f"Параллельный просмотр — {self._station_name()}")
        self.canvas.draw_idle()

    def _refresh_measurement_curve_combo(self) -> None:
        previous_uid = self.measure_curve_combo.currentData()
        self.measure_curve_combo.clear()
        if not self.curves:
            self.measure_curve_combo.addItem("Нет построенных кривых", None)
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
            self.statusBar().showMessage("Не удалось определить кривую для измерения", 3000)
            return

        self._set_cursor(target, curve, sample_index)
        self._redraw_cursors()
        self._update_measurement_info()
        self.canvas.draw_idle()

    def _set_cursor(self, cursor: CursorState, curve: CurveRef, sample_index: int) -> None:
        idx = int(sample_index)
        cursor.ax = curve.ax
        cursor.curve = curve
        cursor.sample_index = idx
        cursor.x_ms = float(curve.time_ms[idx])
        cursor.y_value = float(curve.values[idx])

    def _pick_curve_and_index(self, event: Any) -> tuple[Optional[CurveRef], Optional[int]]:
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

        candidates = [curve for curve in self.curves if curve.ax is event.inaxes]
        if not candidates:
            return None, None

        x_click = float(event.xdata)
        y_click = None if event.ydata is None else float(event.ydata)
        best_curve: Optional[CurveRef] = None
        best_idx: Optional[int] = None
        best_score = float("inf")

        for curve in candidates:
            idx = int(np.argmin(np.abs(curve.time_ms - x_click)))
            x_val = float(curve.time_ms[idx])
            y_val = float(curve.values[idx])

            if y_click is None:
                score = abs(x_val - x_click)
            else:
                x0, x1 = curve.ax.get_xlim()
                y0, y1 = curve.ax.get_ylim()
                x_span = max(abs(x1 - x0), 1e-12)
                y_span = max(abs(y1 - y0), 1e-12)
                score = abs(x_val - x_click) / x_span + abs(y_val - y_click) / y_span

            if score < best_score:
                best_score = score
                best_curve = curve
                best_idx = idx

        return best_curve, best_idx

    def _click_near_cursor_line(self, event: Any, cursor: CursorState, threshold_px: float = 8.0) -> bool:
        if not cursor.is_set or cursor.ax is None or cursor.x_ms is None:
            return False
        if event.inaxes is not cursor.ax:
            return False
        if event.x is None:
            return False
        y_mid = float(np.mean(cursor.ax.get_ylim()))
        x_disp, _ = cursor.ax.transData.transform((cursor.x_ms, y_mid))
        return abs(float(event.x) - float(x_disp)) <= threshold_px

    def _redraw_cursors(self) -> None:
        self.cursor1.remove_artists()
        self.cursor2.remove_artists()
        self._clear_annotation_artists()

        for cursor in (self.cursor1, self.cursor2):
            if not cursor.is_set:
                continue
            assert cursor.ax is not None and cursor.x_ms is not None and cursor.y_value is not None
            vline = cursor.ax.axvline(cursor.x_ms, color=cursor.color, linestyle="--", linewidth=1.8, alpha=0.9)
            marker, = cursor.ax.plot(cursor.x_ms, cursor.y_value, marker="o", markersize=7, color=cursor.color)
            text = cursor.ax.annotate(
                f"{cursor.name}\n{cursor.x_ms:.3f} мс\n{cursor.y_value:.6g}",
                xy=(cursor.x_ms, cursor.y_value),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=8,
                bbox=dict(boxstyle="round", fc="white", ec=cursor.color, alpha=0.9),
            )
            cursor.artists.extend([vline, marker, text])

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
        lines = [
            f"ΔT = {stats['delta_t_ms']:.6f} мс",
            f"ΔY = {stats['delta_y']:.6g}",
            f"dY/dT = {stats['avg_rate_per_ms']:.6g} ед/мс",
            f"dY/dt = {stats['avg_rate_per_s']:.6g} ед/с",
        ]
        if "local_rate_abs_max_per_s" in stats:
            lines.append(f"max |dY/dt| = {stats['local_rate_abs_max_per_s']:.6g} ед/с")
        return "\n".join(lines)

    def _calculate_segment_stats(self) -> Optional[dict[str, Any]]:
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
        if not cursor.is_set or cursor.curve is None or cursor.sample_index is None:
            return f"{icon} {cursor.name}: не установлен"
        return (
            f"{icon} {cursor.name}\n"
            f"  Канал: {cursor.curve.label}\n"
            f"  Индекс: {cursor.sample_index}\n"
            f"  T: {cursor.x_ms:.6f} мс\n"
            f"  Y: {cursor.y_value:.6g}"
        )

    def _update_measurement_info(self) -> None:
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
            block = (
                "Участок между курсорами\n"
                f"  Канал: {stats['curve_label']}\n"
                f"  ΔT: {stats['delta_t_ms']:.6f} мс\n"
                f"  ΔY: {stats['delta_y']:.6g}\n"
                f"  Средняя скорость: {stats['avg_rate_per_ms']:.6g} ед/мс\n"
                f"  Средняя скорость: {stats['avg_rate_per_s']:.6g} ед/с\n"
                f"  Минимум Y: {stats['y_min']:.6g}\n"
                f"  Максимум Y: {stats['y_max']:.6g}\n"
                f"  Отсчётов: {stats['samples']}"
            )
            parts.append(block)
            if 'local_rate_abs_max_per_s' in stats:
                max_pair = stats.get('max_pair')
                extra = (
                    "Локальные скорости на участке\n"
                    f"  Мин dY/dt: {stats['local_rate_min_per_s']:.6g} ед/с\n"
                    f"  Макс dY/dt: {stats['local_rate_max_per_s']:.6g} ед/с\n"
                    f"  Макс |dY/dt|: {stats['local_rate_abs_max_per_s']:.6g} ед/с"
                )
                if isinstance(max_pair, dict):
                    extra += (
                        f"\n  Пара max |dY/dt|: [{max_pair['i_start']} → {max_pair['i_end']}]"
                        f"\n  Интервал: {max_pair['t_start_ms']:.6f} ... {max_pair['t_end_ms']:.6f} мс"
                    )
                parts.append(extra)

        self.cursor_info.setPlainText("\n\n".join(parts))

    def export_measurements_csv(self) -> None:
        if self.comtrade_file is None:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите COMTRADE файл.")
            return

        default_name = "measurements.csv"
        if self.current_cfg_path is not None:
            default_name = f"{self.current_cfg_path.stem}_measurements.csv"

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить CSV",
            str(Path.home() / default_name),
            "CSV файлы (*.csv)",
        )
        if not file_name:
            return

        try:
            stats = self._calculate_segment_stats()
            with open(file_name, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=';')
                writer.writerow(["section", "key", "value"])

                writer.writerow(["file", "cfg_name", self.current_cfg_path.name if self.current_cfg_path else ""])
                writer.writerow(["file", "station", self._station_name()])
                writer.writerow(["file", "device", self._device_name()])
                writer.writerow(["file", "frequency_hz", self._frequency()])
                writer.writerow(["file", "samples", self._time_ms().size])

                for cursor in (self.cursor1, self.cursor2):
                    prefix = cursor.name.lower().replace(" ", "_")
                    writer.writerow([prefix, "is_set", int(cursor.is_set)])
                    writer.writerow([prefix, "curve_label", cursor.curve.label if cursor.curve else ""])
                    writer.writerow([prefix, "sample_index", "" if cursor.sample_index is None else cursor.sample_index])
                    writer.writerow([prefix, "time_ms", "" if cursor.x_ms is None else f"{cursor.x_ms:.12g}"])
                    writer.writerow([prefix, "value", "" if cursor.y_value is None else f"{cursor.y_value:.12g}"])

                if stats is not None:
                    for key in [
                        "curve_label",
                        "delta_t_ms",
                        "delta_y",
                        "avg_rate_per_ms",
                        "avg_rate_per_s",
                        "y_min",
                        "y_max",
                        "samples",
                    ]:
                        writer.writerow(["segment", key, stats[key]])

                    for key in [
                        "local_rate_min_per_s",
                        "local_rate_max_per_s",
                        "local_rate_abs_max_per_s",
                    ]:
                        if key in stats:
                            writer.writerow(["segment", key, stats[key]])

                    max_pair = stats.get("max_pair")
                    if isinstance(max_pair, dict):
                        for key, value in max_pair.items():
                            writer.writerow(["segment_max_dydt_pair", key, value])

                    writer.writerow([])
                    writer.writerow(["segment_table", "sample_index", "time_ms", "value", "local_dYdt_per_s_to_next"])
                    lo = int(stats["lo"])
                    hi = int(stats["hi"])
                    local_rates = stats.get("segment_local_rates_per_s")
                    time_ms = stats["segment_time_ms"]
                    values = stats["segment_values"]
                    for offset, (t_val, y_val) in enumerate(zip(time_ms, values)):
                        idx = lo + offset
                        rate = ""
                        if isinstance(local_rates, np.ndarray) and offset < len(local_rates):
                            rate = f"{float(local_rates[offset]):.12g}"
                        writer.writerow(["segment_table", idx, f"{float(t_val):.12g}", f"{float(y_val):.12g}", rate])

            self.statusBar().showMessage(f"CSV сохранён: {file_name}", 6000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить CSV:\n{exc}")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("COMTRADE Viewer V6.1")
    window = ComtradeViewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

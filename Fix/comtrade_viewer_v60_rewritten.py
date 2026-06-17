#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
COMTRADE Viewer V6.0

Полностью переписанный просмотрщик COMTRADE с исправленной логикой курсоров.

Что исправлено:
- Используется штатный FigureCanvasQTAgg, поэтому координаты клика и данных совпадают корректно.
- Курсоры работают в overlay и parallel режимах по реальным осям Matplotlib.
- Курсоры привязываются к ближайшему отсчёту выбранной кривой, а не к произвольной точке картинки.
- Удаление курсоров выполняется кликом рядом с вертикальной линией курсора.
- При перерисовке старые линии, маркеры и текст удаляются без накопления артефактов.
- Добавлен расчёт параметров участка между курсорами:
  средняя скорость изменения, локальные скорости, длительность, амплитуда.

Зависимости:
    pip install comtrade PyQt6 matplotlib numpy
"""

from __future__ import annotations

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
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)

try:
    from comtrade import Comtrade
except ImportError as exc:
    raise SystemExit(
        "Не найдена библиотека 'comtrade'. Установите: pip install comtrade"
    ) from exc


@dataclass
class CurveRef:
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
        return self.ax is not None and self.sample_index is not None and self.x_ms is not None and self.y_value is not None


class ComtradePlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent: Optional[QWidget] = None):
        self.fig = Figure(figsize=(12, 7), dpi=100, constrained_layout=True)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setFocus()


class ComtradeViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("COMTRADE Viewer V6.0")
        self.resize(1700, 950)

        self.comtrade_file: Optional[Comtrade] = None
        self.plot_mode = "parallel"
        self.curves: list[CurveRef] = []
        self.cursor1 = CursorState(name="Курсор 1", color="red")
        self.cursor2 = CursorState(name="Курсор 2", color="blue")
        self.annotation_artists: list[Artist] = []

        self._init_ui()
        self._connect_canvas_events()
        self._update_measurement_info()

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left.setMaximumWidth(430)

        self.open_btn = QPushButton("📂 Открыть CFG файл")
        self.open_btn.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.open_btn.clicked.connect(self.open_file)
        left_layout.addWidget(self.open_btn)

        self.info_label = QLabel("Файл не загружен")
        self.info_label.setWordWrap(True)
        self.info_label.setFont(QFont("Arial", 9))
        left_layout.addWidget(self.info_label)

        mode_group = QGroupBox("Режим просмотра")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Наложение графиков", "overlay")
        self.mode_combo.addItem("Параллельный просмотр", "parallel")
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        left_layout.addWidget(mode_group)

        channels_group = QGroupBox("Каналы")
        channels_layout = QVBoxLayout(channels_group)
        self.channel_list = QListWidget()
        self.channel_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        channels_layout.addWidget(self.channel_list)
        left_layout.addWidget(channels_group)

        self.plot_btn = QPushButton("📈 Отобразить выбранные")
        self.plot_btn.clicked.connect(self.plot_selected_channels)
        left_layout.addWidget(self.plot_btn)

        self.plot_all_btn = QPushButton("📊 Все аналоговые")
        self.plot_all_btn.clicked.connect(self.plot_all_analog)
        left_layout.addWidget(self.plot_all_btn)

        self.clear_cursor_btn = QPushButton("🗑 Очистить оба курсора")
        self.clear_cursor_btn.clicked.connect(self.clear_cursors)
        left_layout.addWidget(self.clear_cursor_btn)

        help_group = QGroupBox("Работа курсоров")
        help_layout = QVBoxLayout(help_group)
        help_label = QLabel(
            "ЛКМ: установить/удалить Курсор 1\n"
            "ПКМ: установить/удалить Курсор 2\n"
            "Курсор привязывается к ближайшему отсчёту выбранной кривой\n"
            "Удаление срабатывает кликом рядом с вертикальной линией курсора"
        )
        help_label.setWordWrap(True)
        help_layout.addWidget(help_label)
        left_layout.addWidget(help_group)

        measure_group = QGroupBox("Измерения")
        measure_layout = QVBoxLayout(measure_group)
        self.cursor_info = QTextEdit()
        self.cursor_info.setReadOnly(True)
        self.cursor_info.setFont(QFont("Consolas", 9))
        measure_layout.addWidget(self.cursor_info)
        left_layout.addWidget(measure_group, 1)

        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.canvas = ComtradePlotCanvas(self)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 1280])

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
        value = self._get_attr("analog_count", "analogcount", default=0)
        return int(value or 0)

    def _status_count(self) -> int:
        value = self._get_attr("status_count", "statuscount", default=0)
        return int(value or 0)

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
        values = self._get_attr("time", default=[])
        return np.asarray(values, dtype=float)

    def _time_ms(self) -> np.ndarray:
        return self._time_seconds() * 1000.0

    def _analog_values(self, index: int) -> np.ndarray:
        analog = self._get_attr("analog", default=[])
        return np.asarray(analog[index], dtype=float)

    def _status_values(self, index: int) -> np.ndarray:
        status = self._get_attr("status", default=[])
        return np.asarray(status[index], dtype=float)

    def open_file(self) -> None:
        cfg_file, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите COMTRADE CFG файл",
            "",
            "COMTRADE CFG (*.cfg);;Все файлы (*.*)"
        )
        if not cfg_file:
            return

        try:
            ct = Comtrade()
            ct.load(cfg_file)
            self.comtrade_file = ct
            self.update_info()
            self.update_channel_list()
            self.clear_plot(keep_message=True)
            self.statusBar().showMessage(f"Загружен файл: {Path(cfg_file).name}", 5000)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{exc}")

    def update_info(self) -> None:
        if self.comtrade_file is None:
            self.info_label.setText("Файл не загружен")
            return

        time_ms = self._time_ms()
        duration_ms = float(time_ms[-1]) if time_ms.size else 0.0
        text = (
            f"📊 Информация о файле\n"
            f"Станция: {self._station_name()}\n"
            f"Устройство: {self._device_name()}\n\n"
            f"📈 Каналы\n"
            f"Аналоговые: {self._analog_count()}\n"
            f"Статусные: {self._status_count()}\n\n"
            f"⏱ Параметры\n"
            f"Частота: {self._frequency()} Гц\n"
            f"Отсчётов: {time_ms.size}\n"
            f"Длительность: {duration_ms:.3f} мс"
        )
        self.info_label.setText(text)

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

        try:
            items = [item.data(Qt.ItemDataRole.UserRole) for item in selected]
            if self.plot_mode == "overlay":
                self._plot_overlay(items)
            else:
                self._plot_parallel(items)
            self._redraw_cursors()
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
            self._redraw_cursors()
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Ошибка", f"Ошибка построения графика:\n{exc}")

    def _channel_name(self, channel_type: str, channel_index: int) -> str:
        if channel_type == "analog":
            names = self._names_analog()
            return names[channel_index] if channel_index < len(names) else f"Analog_{channel_index}"
        names = self._names_status()
        return names[channel_index] if channel_index < len(names) else f"Status_{channel_index}"

    def _channel_values(self, channel_type: str, channel_index: int) -> np.ndarray:
        return self._analog_values(channel_index) if channel_type == "analog" else self._status_values(channel_index)

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
            self.curves.append(CurveRef(ax=ax, line=line, channel_type=channel_type, channel_index=channel_index, label=label, time_ms=time_ms, values=values))

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
            self.curves.append(CurveRef(ax=ax, line=line, channel_type=channel_type, channel_index=channel_index, label=label, time_ms=time_ms, values=values))

        display_title = title or f"Параллельный просмотр — {self._station_name()}"
        self.canvas.fig.suptitle(display_title)
        self.canvas.draw_idle()

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
            return

        target.ax = curve.ax
        target.curve = curve
        target.sample_index = int(sample_index)
        target.x_ms = float(curve.time_ms[sample_index])
        target.y_value = float(curve.values[sample_index])

        self._redraw_cursors()
        self._update_measurement_info()
        self.canvas.draw_idle()

    def _pick_curve_and_index(self, event: Any) -> tuple[Optional[CurveRef], Optional[int]]:
        candidates = [curve for curve in self.curves if curve.ax is event.inaxes]
        if not candidates or event.xdata is None:
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
                dx = abs(x_val - x_click) / x_span
                dy = abs(y_val - y_click) / y_span
                score = dx + dy

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
            if not cursor.is_set or cursor.ax is None or cursor.x_ms is None or cursor.y_value is None:
                continue
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
        return (
            f"ΔT = {stats['delta_t_ms']:.3f} мс\n"
            f"ΔY = {stats['delta_y']:.6g}\n"
            f"dY/dT = {stats['avg_rate_per_ms']:.6g} ед/мс\n"
            f"dY/dt = {stats['avg_rate_per_s']:.6g} ед/с"
        )

    def _calculate_segment_stats(self) -> Optional[dict[str, float]]:
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

        t_seg_ms = curve.time_ms[lo:hi + 1]
        y_seg = curve.values[lo:hi + 1]
        dt_ms = np.diff(t_seg_ms)
        dy = np.diff(y_seg)
        valid = np.abs(dt_ms) > 0

        local_rates_per_ms = np.array([], dtype=float)
        local_rates_per_s = np.array([], dtype=float)
        if np.any(valid):
            local_rates_per_ms = dy[valid] / dt_ms[valid]
            local_rates_per_s = dy[valid] / (dt_ms[valid] / 1000.0)

        avg_rate_per_ms = delta_y / delta_t_ms
        avg_rate_per_s = delta_y / (delta_t_ms / 1000.0)

        stats = {
            "delta_t_ms": delta_t_ms,
            "delta_y": delta_y,
            "avg_rate_per_ms": avg_rate_per_ms,
            "avg_rate_per_s": avg_rate_per_s,
            "y_min": float(np.min(y_seg)),
            "y_max": float(np.max(y_seg)),
            "samples": float(len(y_seg)),
        }
        if local_rates_per_ms.size:
            stats.update({
                "local_rate_min_per_ms": float(np.min(local_rates_per_ms)),
                "local_rate_max_per_ms": float(np.max(local_rates_per_ms)),
                "local_rate_abs_max_per_ms": float(np.max(np.abs(local_rates_per_ms))),
                "local_rate_min_per_s": float(np.min(local_rates_per_s)),
                "local_rate_max_per_s": float(np.max(local_rates_per_s)),
                "local_rate_abs_max_per_s": float(np.max(np.abs(local_rates_per_s))),
            })
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
            "ИЗМЕРЕНИЯ КУРСОРОВ\n" + "=" * 54,
            self._cursor_block(self.cursor1, "🔴"),
            self._cursor_block(self.cursor2, "🔵"),
        ]

        stats = self._calculate_segment_stats()
        if stats is None:
            if self.cursor1.is_set and self.cursor2.is_set:
                parts.append(
                    "Участок между курсорами\n"
                    "  Для расчёта оба курсора должны стоять на одной и той же кривой."
                )
        else:
            parts.append(
                "Участок между курсорами\n"
                f"  ΔT: {stats['delta_t_ms']:.6f} мс\n"
                f"  ΔY: {stats['delta_y']:.6g}\n"
                f"  Средняя скорость: {stats['avg_rate_per_ms']:.6g} ед/мс\n"
                f"  Средняя скорость: {stats['avg_rate_per_s']:.6g} ед/с\n"
                f"  Минимум Y на участке: {stats['y_min']:.6g}\n"
                f"  Максимум Y на участке: {stats['y_max']:.6g}\n"
                f"  Отсчётов в участке: {int(stats['samples'])}"
            )
            if 'local_rate_abs_max_per_s' in stats:
                parts.append(
                    "Локальная скорость на участке\n"
                    f"  Мин dY/dt: {stats['local_rate_min_per_s']:.6g} ед/с\n"
                    f"  Макс dY/dt: {stats['local_rate_max_per_s']:.6g} ед/с\n"
                    f"  Макс |dY/dt|: {stats['local_rate_abs_max_per_s']:.6g} ед/с"
                )

        self.cursor_info.setPlainText("\n\n".join(parts))


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("COMTRADE Viewer V6.0")
    window = ComtradeViewer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

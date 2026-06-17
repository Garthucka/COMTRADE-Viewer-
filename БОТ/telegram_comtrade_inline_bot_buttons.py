from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from comtrade import Comtrade
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "tg_comtrade_files"
DATA_DIR.mkdir(exist_ok=True)
PAGE_SIZE = 12


@dataclass
class CurveRef:
    uid: str
    channel_type: str
    channel_index: int
    label: str
    time_ms: np.ndarray
    values: np.ndarray


@dataclass
class CursorState:
    name: str
    curve_uid: Optional[str] = None
    sample_index: Optional[int] = None
    x_ms: Optional[float] = None
    y_value: Optional[float] = None

    @property
    def is_set(self) -> bool:
        return self.curve_uid is not None and self.sample_index is not None and self.x_ms is not None and self.y_value is not None

    def clear(self) -> None:
        self.curve_uid = None
        self.sample_index = None
        self.x_ms = None
        self.y_value = None


class SessionState:
    def __init__(self) -> None:
        self.rec: Optional[Comtrade] = None
        self.cfg_path: Optional[Path] = None
        self.dat_path: Optional[Path] = None
        self.plot_mode: str = "parallel"
        self.curves: list[CurveRef] = []
        self.cursor1 = CursorState("Курсор 1")
        self.cursor2 = CursorState("Курсор 2")
        self.selected_channels: set[str] = set()
        self.panel_page: int = 0
        self.pending_cursor_no: Optional[int] = None
        self.pending_cursor_uid: Optional[str] = None

    def load_cfg(self, cfg_path: Path) -> None:
        rec = Comtrade()
        rec.load(str(cfg_path), encoding="cp1251")
        self.rec = rec
        self.cfg_path = cfg_path
        dat_path = cfg_path.with_suffix('.dat')
        self.dat_path = dat_path if dat_path.exists() else None
        self.curves.clear()
        self.cursor1.clear()
        self.cursor2.clear()
        self.selected_channels.clear()
        self.panel_page = 0
        self.pending_cursor_no = None
        self.pending_cursor_uid = None

    def _get_attr(self, *names: str, default: Any = None) -> Any:
        if self.rec is None:
            return default
        for name in names:
            if hasattr(self.rec, name):
                return getattr(self.rec, name)
        return default

    def analog_count(self) -> int:
        return int(self._get_attr("analog_count", "analogcount", default=0) or 0)

    def status_count(self) -> int:
        return int(self._get_attr("status_count", "statuscount", default=0) or 0)

    def names_analog(self) -> list[str]:
        names = self._get_attr("analog_channel_ids", "names_analog", "analog_channel_names", default=[])
        return list(names) if names is not None else []

    def names_status(self) -> list[str]:
        names = self._get_attr("status_channel_ids", "names_status", "status_channel_names", default=[])
        return list(names) if names is not None else []

    def station_name(self) -> str:
        return str(self._get_attr("station_name", "stationname", default="Unknown"))

    def device_name(self) -> str:
        return str(self._get_attr("rec_dev_id", "recdevid", default="Unknown"))

    def time_ms(self) -> np.ndarray:
        t = np.asarray(self._get_attr("time", default=[]), dtype=float)
        return t * 1000.0

    def analog_values(self, index: int) -> np.ndarray:
        return np.asarray(self._get_attr("analog", default=[])[index], dtype=float)

    def status_values(self, index: int) -> np.ndarray:
        return np.asarray(self._get_attr("status", default=[])[index], dtype=float)

    def channel_name(self, channel_type: str, channel_index: int) -> str:
        if channel_type == "analog":
            names = self.names_analog()
            return names[channel_index] if channel_index < len(names) else f"Analog_{channel_index}"
        names = self.names_status()
        return names[channel_index] if channel_index < len(names) else f"Status_{channel_index}"

    def channel_values(self, channel_type: str, channel_index: int) -> np.ndarray:
        return self.analog_values(channel_index) if channel_type == "analog" else self.status_values(channel_index)

    def all_channel_tokens(self) -> list[str]:
        tokens = [f"A{i}" for i in range(self.analog_count())]
        tokens += [f"S{i}" for i in range(self.status_count())]
        return tokens

    def parse_channel_token(self, token: str) -> tuple[str, int]:
        token = token.strip().upper()
        if token.startswith('A'):
            return 'analog', int(token[1:])
        if token.startswith('S'):
            return 'status', int(token[1:])
        raise ValueError(f"Неверный канал: {token}")

    def token_label(self, token: str) -> str:
        channel_type, channel_index = self.parse_channel_token(token)
        return f"{token}: {self.channel_name(channel_type, channel_index)}"

    def build_curves(self, items: list[tuple[str, int]]) -> list[CurveRef]:
        t = self.time_ms()
        curves: list[CurveRef] = []
        for channel_type, channel_index in items:
            label = self.channel_name(channel_type, channel_index)
            values = self.channel_values(channel_type, channel_index)
            uid = f"{channel_type}:{channel_index}"
            curves.append(CurveRef(uid, channel_type, channel_index, label, t, values))
        self.curves = curves
        self.cursor1.clear()
        self.cursor2.clear()
        self.pending_cursor_no = None
        self.pending_cursor_uid = None
        return curves

    def curve_by_uid(self, uid: Optional[str]) -> Optional[CurveRef]:
        if uid is None:
            return None
        for curve in self.curves:
            if curve.uid == uid:
                return curve
        return None

    def curve_by_channel(self, channel_type: str, channel_index: int) -> Optional[CurveRef]:
        return self.curve_by_uid(f"{channel_type}:{channel_index}")

    def set_cursor(self, cursor_no: int, curve_uid: str, time_ms_value: float) -> dict[str, Any]:
        curve = self.curve_by_uid(curve_uid)
        if curve is None:
            raise ValueError("Сначала постройте график с нужным каналом.")
        idx = int(np.argmin(np.abs(curve.time_ms - float(time_ms_value))))
        target = self.cursor1 if cursor_no == 1 else self.cursor2
        target.curve_uid = curve.uid
        target.sample_index = idx
        target.x_ms = float(curve.time_ms[idx])
        target.y_value = float(curve.values[idx])
        self.pending_cursor_no = None
        self.pending_cursor_uid = None
        return {
            "label": curve.label,
            "uid": curve.uid,
            "sample_index": idx,
            "time_ms": target.x_ms,
            "value": target.y_value,
        }

    def calculate_segment_stats(self) -> Optional[dict[str, Any]]:
        if not (self.cursor1.is_set and self.cursor2.is_set):
            return None
        if self.cursor1.curve_uid != self.cursor2.curve_uid:
            return None
        curve = self.curve_by_uid(self.cursor1.curve_uid)
        if curve is None:
            return None
        i1 = int(self.cursor1.sample_index)
        i2 = int(self.cursor2.sample_index)
        lo, hi = sorted((i1, i2))
        x1 = float(curve.time_ms[i1])
        x2 = float(curve.time_ms[i2])
        y1 = float(curve.values[i1])
        y2 = float(curve.values[i2])
        delta_t_ms = x2 - x1
        if abs(delta_t_ms) < 1e-15:
            return None
        delta_y = y2 - y1
        t_seg_ms = np.asarray(curve.time_ms[lo:hi + 1], dtype=float)
        y_seg = np.asarray(curve.values[lo:hi + 1], dtype=float)
        dt_ms = np.diff(t_seg_ms)
        dy = np.diff(y_seg)
        valid = np.abs(dt_ms) > 0
        stats = {
            "curve_uid": curve.uid,
            "curve_label": curve.label,
            "delta_t_ms": delta_t_ms,
            "delta_y": delta_y,
            "avg_rate_per_ms": delta_y / delta_t_ms,
            "avg_rate_per_s": delta_y / (delta_t_ms / 1000.0),
            "y_min": float(np.min(y_seg)),
            "y_max": float(np.max(y_seg)),
            "samples": int(len(y_seg)),
        }
        if np.any(valid):
            local_rates_per_ms = dy[valid] / dt_ms[valid]
            local_rates_per_s = dy[valid] / (dt_ms[valid] / 1000.0)
            stats.update({
                "local_rate_min_per_ms": float(np.min(local_rates_per_ms)),
                "local_rate_max_per_ms": float(np.max(local_rates_per_ms)),
                "local_rate_abs_max_per_ms": float(np.max(np.abs(local_rates_per_ms))),
                "local_rate_min_per_s": float(np.min(local_rates_per_s)),
                "local_rate_max_per_s": float(np.max(local_rates_per_s)),
                "local_rate_abs_max_per_s": float(np.max(np.abs(local_rates_per_s))),
            })
        return stats

    def export_measurements_csv(self) -> Path:
        if self.cfg_path is None:
            raise ValueError("Файл не загружен")
        out_path = DATA_DIR / f"{self.cfg_path.stem}_measurements.csv"
        stats = self.calculate_segment_stats()
        with open(out_path, "w", newline="", encoding="cp1251") as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(["section", "key", "value"])
            writer.writerow(["file", "cfg_name", self.cfg_path.name])
            writer.writerow(["file", "device", self.device_name()])
            for cursor in (self.cursor1, self.cursor2):
                prefix = cursor.name.lower().replace(' ', '_')
                curve = self.curve_by_uid(cursor.curve_uid)
                writer.writerow([prefix, "is_set", int(cursor.is_set)])
                writer.writerow([prefix, "curve_label", curve.label if curve else ""])
                writer.writerow([prefix, "sample_index", "" if cursor.sample_index is None else cursor.sample_index])
                writer.writerow([prefix, "time_ms", "" if cursor.x_ms is None else f"{cursor.x_ms:.6g}"])
                writer.writerow([prefix, "value", "" if cursor.y_value is None else f"{cursor.y_value:.4g}"])
            if stats is not None:
                for key in ["curve_label", "delta_t_ms", "delta_y", "avg_rate_per_ms", "avg_rate_per_s", "y_min", "y_max"]:
                    value = stats[key]
                    if isinstance(value, (int, float)) and key != "curve_label":
                        value = f"{value:.4g}"
                    writer.writerow(["segment", key, value])
        return out_path

    def render_plot(self) -> Path:
        if not self.curves:
            raise ValueError("Нет построенных кривых")
        fig = plt.figure(figsize=(14, 8), dpi=130, constrained_layout=True)
        colors = ["tab:blue", "tab:red", "tab:green", "tab:orange", "tab:purple", "tab:brown", "tab:pink", "tab:cyan"]
        if self.plot_mode == "overlay":
            ax = fig.add_subplot(111)
            for i, curve in enumerate(self.curves):
                ax.plot(curve.time_ms, curve.values, linewidth=1.3, color=colors[i % len(colors)], label=curve.label)
            ax.set_title(f"Наложение графиков — {self.station_name()}")
            ax.set_xlabel("Время, мс")
            ax.set_ylabel("Значение")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best", fontsize=8)
            axes = [ax]
        else:
            axes = []
            shared_ax = None
            for row, curve in enumerate(self.curves, start=1):
                ax = fig.add_subplot(len(self.curves), 1, row, sharex=shared_ax)
                if shared_ax is None:
                    shared_ax = ax
                color = "tab:blue" if curve.channel_type == "analog" else "tab:red"
                ax.plot(curve.time_ms, curve.values, linewidth=1.2, color=color)
                ax.set_ylabel(curve.label)
                ax.grid(True, alpha=0.3)
                if row == len(self.curves):
                    ax.set_xlabel("Время, мс")
                else:
                    ax.tick_params(labelbottom=False)
                axes.append(ax)
            fig.suptitle(f"Параллельный просмотр — {self.station_name()}")
        self._draw_cursor_annotations(axes)
        out_path = DATA_DIR / f"plot_{os.getpid()}_{id(self)}.png"
        fig.savefig(out_path, bbox_inches="tight")
        plt.close(fig)
        return out_path

    def _draw_cursor_annotations(self, axes: list[Any]) -> None:
        for cursor, color in ((self.cursor1, "red"), (self.cursor2, "blue")):
            if not cursor.is_set:
                continue
            curve = self.curve_by_uid(cursor.curve_uid)
            if curve is None:
                continue
            for ax in axes:
                if self.plot_mode == "overlay" or ax.get_ylabel() == curve.label:
                    ax.axvline(cursor.x_ms, color=color, linestyle="--", linewidth=1.5, alpha=0.9)
                    ax.plot(cursor.x_ms, cursor.y_value, marker="o", markersize=6, color=color)
                    ax.annotate(
                        f"{cursor.name}\n{cursor.x_ms:.3f} мс\n{cursor.y_value:.6g}",
                        xy=(cursor.x_ms, cursor.y_value),
                        xytext=(8, 8),
                        textcoords="offset points",
                        fontsize=8,
                        bbox=dict(boxstyle="round", fc="white", ec=color, alpha=0.9),
                    )
        stats = self.calculate_segment_stats()
        if stats and axes:
            axes[0].text(
                0.015,
                0.985,
                f"ΔT = {stats['delta_t_ms']:.2f} мс\nΔY = {stats['delta_y']:.4g}\ndY/dT = {stats['avg_rate_per_ms']:.4g}\ndY/dt = {stats['avg_rate_per_s']:.4g}",
                transform=axes[0].transAxes,
                va="top",
                ha="left",
                fontsize=8.5,
                family="monospace",
                bbox=dict(boxstyle="round", fc="lightyellow", ec="black", alpha=0.92),
            )


def get_session(context: ContextTypes.DEFAULT_TYPE) -> SessionState:
    session = context.user_data.get("session")
    if session is None:
        session = SessionState()
        context.user_data["session"] = session
    return session


def token() -> str:
    value = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not value:
        raise RuntimeError("Не задан TELEGRAM_BOT_TOKEN")
    return value


def channel_panel_text(session: SessionState) -> str:
    if session.rec is None:
        return "Сначала загрузите пару COMTRADE файлов .cfg + .dat."
    tokens = session.all_channel_tokens()
    total = len(tokens)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(0, session.panel_page), pages - 1)
    session.panel_page = page
    selected = sorted(session.selected_channels)
    selected_text = ", ".join(selected[:20]) if selected else "ничего не выбрано"
    built = ", ".join([f"{c.channel_type[0].upper()}{c.channel_index}".replace('A', 'A').replace('S', 'S') for c in session.curves])
    built_text = built if built else "график еще не построен"
    return (
        f"Файл: {session.cfg_path.name}\n"
        f"Устройство: {session.device_name()}\n"
        f"Режим: {session.plot_mode}\n"
        f"Страница: {page + 1}/{pages}\n"
        f"Выбрано: {selected_text}\n"
        f"Построено: {built_text}\n\n"
        f"Отмечайте каналы кнопками ниже, затем нажмите 'Построить выбранные'."
    )


def channel_panel_markup(session: SessionState) -> InlineKeyboardMarkup:
    tokens = session.all_channel_tokens()
    total = len(tokens)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(0, session.panel_page), pages - 1)
    start = page * PAGE_SIZE
    subset = tokens[start:start + PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(subset), 2):
        row_tokens = subset[i:i + 2]
        row = []
        for token_value in row_tokens:
            mark = "✅" if token_value in session.selected_channels else "☑️"
            row.append(InlineKeyboardButton(f"{mark} {token_value}", callback_data=f"sel:{token_value}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("Overlay", callback_data="mode:overlay"),
        InlineKeyboardButton("Parallel", callback_data="mode:parallel"),
    ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data="page:prev"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data="page:next"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("📈 Построить выбранные", callback_data="plot:selected"),
        InlineKeyboardButton("📊 Все аналоговые", callback_data="plot:all"),
    ])
    rows.append([
        InlineKeyboardButton("🧹 Очистить выбор", callback_data="sel:clear"),
        InlineKeyboardButton("🎯 Курсоры", callback_data="cursor:menu"),
    ])
    rows.append([
        InlineKeyboardButton("📄 CSV", callback_data="export:csv"),
        InlineKeyboardButton("📏 Измерение", callback_data="measure:show"),
    ])
    return InlineKeyboardMarkup(rows)


def cursor_menu_text(session: SessionState) -> str:
    if not session.curves:
        return "Сначала постройте график. После этого можно выбрать кривую для курсора 1 или 2."
    lines = ["Выберите кривую для установки курсора 1 или 2."]
    if session.cursor1.is_set:
        lines.append(f"Курсор 1: T={session.cursor1.x_ms:.3f} мс, Y={session.cursor1.y_value:.6g}")
    if session.cursor2.is_set:
        lines.append(f"Курсор 2: T={session.cursor2.x_ms:.3f} мс, Y={session.cursor2.y_value:.6g}")
    return "\n".join(lines)


def cursor_menu_markup(session: SessionState) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for curve in session.curves[:30]:
        token_value = f"A{curve.channel_index}" if curve.channel_type == "analog" else f"S{curve.channel_index}"
        short = curve.label[:18]
        rows.append([
            InlineKeyboardButton(f"🔴 {token_value}", callback_data=f"curpick:1:{curve.uid}"),
            InlineKeyboardButton(f"🔵 {token_value}", callback_data=f"curpick:2:{curve.uid}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="panel:show"),
        InlineKeyboardButton("🗑 Очистить курсоры", callback_data="cursor:clear"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_plot(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session: SessionState, caption: str) -> None:
    path = session.render_plot()
    with open(path, "rb") as f:
        await context.bot.send_photo(chat_id=chat_id, photo=InputFile(f, filename=path.name), caption=caption)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Бот COMTRADE с inline-кнопками готов. Отправьте .cfg и .dat файлы.\n\n"
        "Основное:\n"
        "/panel - панель выбора каналов\n"
        "/info - сведения о файле\n"
        "/measure - расчёт по курсорам\n"
        "/exportcsv - отправить CSV\n\n"
        "После построения графика откройте /panel и нажмите 'Курсоры'."
    )
    await update.message.reply_text(text)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.document is None:
        return
    session = get_session(context)
    doc = update.message.document
    filename = doc.file_name or "unnamed.bin"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".cfg", ".dat"}:
        await update.message.reply_text("Нужны файлы COMTRADE: .cfg и .dat")
        return
    chat_dir = DATA_DIR / str(update.effective_chat.id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    save_path = chat_dir / filename
    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(custom_path=str(save_path))
    stem = Path(filename).stem
    cfg_path = chat_dir / f"{stem}.cfg"
    dat_path = chat_dir / f"{stem}.dat"
    if suffix == ".cfg" and dat_path.exists():
        session.load_cfg(save_path)
        await update.message.reply_text("Пара COMTRADE загружена. Откройте /panel для выбора каналов.")
    elif suffix == ".dat" and cfg_path.exists():
        session.load_cfg(cfg_path)
        await update.message.reply_text("Пара COMTRADE загружена. Откройте /panel для выбора каналов.")
    else:
        await update.message.reply_text(f"Файл сохранён: {filename}. Дождитесь второй части пары COMTRADE.")


async def panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(context)
    await update.message.reply_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))


async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(context)
    if session.rec is None:
        await update.message.reply_text("Файл ещё не загружен.")
        return
    t = session.time_ms()
    duration_ms = float(t[-1]) if t.size else 0.0
    text = (
        f"Файл: {session.cfg_path.name}\n"
        f"Устройство: {session.device_name()}\n"
        f"Аналоговые каналы: {session.analog_count()}\n"
        f"Дискретные каналы: {session.status_count()}\n"
        f"Длительность: {duration_ms:.1f} мс"
    )
    await update.message.reply_text(text)


async def measure_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(context)
    stats = session.calculate_segment_stats()
    if stats is None:
        await update.message.reply_text("Для расчёта оба курсора должны быть установлены на одну и ту же построенную кривую.")
        return
    text = (
        f"Канал: {stats['curve_label']}\n"
        f"ΔT: {stats['delta_t_ms']:.2f} мс\n"
        f"ΔY: {stats['delta_y']:.4g}\n"
        f"Средняя скорость: {stats['avg_rate_per_ms']:.4g} ед/мс\n"
        f"Средняя скорость: {stats['avg_rate_per_s']:.4g} ед/с\n"
        f"Ymin: {stats['y_min']:.4g}\n"
        f"Ymax: {stats['y_max']:.4g}\n"
        f"Отсчётов в участке: {stats['samples']}"
    )
    await update.message.reply_text(text)


async def exportcsv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = get_session(context)
    try:
        path = session.export_measurements_csv()
    except Exception as exc:
        await update.message.reply_text(f"Ошибка экспорта: {exc}")
        return
    with open(path, "rb") as f:
        await update.message.reply_document(document=InputFile(f, filename=path.name), caption="CSV измерений готов.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return
    session = get_session(context)
    if session.pending_cursor_no is None or session.pending_cursor_uid is None:
        return
    try:
        time_ms_value = float(update.message.text.replace(',', '.').strip())
        result = session.set_cursor(session.pending_cursor_no, session.pending_cursor_uid, time_ms_value)
    except Exception as exc:
        await update.message.reply_text(f"Ошибка установки курсора: {exc}")
        return
    await send_plot(update.effective_chat.id, context, session, f"Курсор {session.pending_cursor_no or ''}: {result['label']}, T={result['time_ms']:.3f} мс, Y={result['value']:.6g}")
    stats = session.calculate_segment_stats()
    if stats is not None:
        await update.message.reply_text(
            f"Участок: {stats['curve_label']}\nΔT={stats['delta_t_ms']:.2f} мс\nΔY={stats['delta_y']:.4g}\ndY/dt={stats['avg_rate_per_s']:.4g} ед/с"
        )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    session = get_session(context)
    data = query.data or ""

    if data == "noop":
        return

    if data == "panel:show":
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data.startswith("page:"):
        direction = data.split(":", 1)[1]
        if direction == "next":
            session.panel_page += 1
        elif direction == "prev":
            session.panel_page -= 1
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data.startswith("mode:"):
        session.plot_mode = data.split(":", 1)[1]
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data == "sel:clear":
        session.selected_channels.clear()
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data.startswith("sel:"):
        token_value = data.split(":", 1)[1]
        if token_value in session.selected_channels:
            session.selected_channels.remove(token_value)
        else:
            session.selected_channels.add(token_value)
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data == "plot:all":
        if session.rec is None:
            await query.message.reply_text("Файл ещё не загружен.")
            return
        if session.analog_count() == 0:
            await query.message.reply_text("В файле нет аналоговых каналов.")
            return
        items = [("analog", i) for i in range(session.analog_count())]
        session.build_curves(items)
        await send_plot(query.message.chat_id, context, session, "Построены все аналоговые каналы.")
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data == "plot:selected":
        if session.rec is None:
            await query.message.reply_text("Файл ещё не загружен.")
            return
        if not session.selected_channels:
            await query.message.reply_text("Сначала отметьте хотя бы один канал.")
            return
        items = [session.parse_channel_token(token_value) for token_value in sorted(session.selected_channels)]
        session.build_curves(items)
        await send_plot(query.message.chat_id, context, session, "Построен график выбранных каналов.")
        await query.edit_message_text(channel_panel_text(session), reply_markup=channel_panel_markup(session))
        return

    if data == "cursor:menu":
        await query.edit_message_text(cursor_menu_text(session), reply_markup=cursor_menu_markup(session))
        return

    if data == "cursor:clear":
        session.cursor1.clear()
        session.cursor2.clear()
        session.pending_cursor_no = None
        session.pending_cursor_uid = None
        await query.edit_message_text(cursor_menu_text(session), reply_markup=cursor_menu_markup(session))
        return

    if data.startswith("curpick:"):
        _, cursor_no, curve_uid = data.split(":", 2)
        session.pending_cursor_no = int(cursor_no)
        session.pending_cursor_uid = curve_uid
        curve = session.curve_by_uid(curve_uid)
        label = curve.label if curve else curve_uid
        await query.message.reply_text(f"Выбран {label} для курсора {cursor_no}. Теперь отправьте время в мс обычным сообщением, например: 12.5")
        return

    if data == "measure:show":
        stats = session.calculate_segment_stats()
        if stats is None:
            await query.message.reply_text("Для расчёта оба курсора должны быть установлены на одну и ту же построенную кривую.")
        else:
            await query.message.reply_text(
                f"Канал: {stats['curve_label']}\nΔT: {stats['delta_t_ms']:.2f} мс\nΔY: {stats['delta_y']:.4g}\nСредняя скорость: {stats['avg_rate_per_s']:.4g} ед/с"
            )
        return

    if data == "export:csv":
        try:
            path = session.export_measurements_csv()
        except Exception as exc:
            await query.message.reply_text(f"Ошибка экспорта: {exc}")
            return
        with open(path, "rb") as f:
            await query.message.reply_document(document=InputFile(f, filename=path.name), caption="CSV измерений готов.")
        return


def main() -> None:
    app = Application.builder().token(token()).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel_cmd))
    app.add_handler(CommandHandler("info", info_cmd))
    app.add_handler(CommandHandler("measure", measure_cmd))
    app.add_handler(CommandHandler("exportcsv", exportcsv_cmd))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

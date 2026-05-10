"""
Profile page – equity curve + daily P&L charts for each user.
Admin can switch between users; regular users see only themselves.
"""
from __future__ import annotations

import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt

# Matplotlib with Qt6 backend
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import matplotlib.ticker as mticker

from trade_relay.i18n import t
from trade_relay.auth.manager import Session
from trade_relay import database as db


# ── dark style for matplotlib ────────────────────────────────────────────────
_BG      = "#0d1117"
_AXES_BG = "#161b22"
_GRID    = "#21262d"
_TEXT    = "#c9d1d9"
_GREEN   = "#3fb950"
_RED     = "#f85149"
_BLUE    = "#388bfd"


def _apply_dark_style(fig: Figure, ax) -> None:
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_AXES_BG)
    ax.tick_params(colors=_TEXT, labelsize=9)
    ax.xaxis.label.set_color(_TEXT)
    ax.yaxis.label.set_color(_TEXT)
    ax.title.set_color(_TEXT)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.grid(color=_GRID, linestyle="--", linewidth=0.5)


# ── Stats cards ──────────────────────────────────────────────────────────────
class _StatCard(QFrame):
    def __init__(self, label: str, value: str = "--") -> None:
        super().__init__()
        self.setObjectName("statCard")
        self.setStyleSheet(
            "#statCard { background:#161b22; border:1px solid #30363d;"
            " border-radius:6px; padding:8px 16px; }"
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(10, 8, 10, 8)

        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        self._val = QLabel(value)
        self._val.setStyleSheet("color:#e6edf3; font-size:18px; font-weight:600;")
        self._val.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._lbl)
        layout.addWidget(self._val)

    def set_value(self, value: str, color: Optional[str] = None) -> None:
        self._val.setText(value)
        style = "font-size:18px; font-weight:600;"
        style += f" color:{color};" if color else " color:#e6edf3;"
        self._val.setStyleSheet(style)


# ── Chart canvas (two subplots) ──────────────────────────────────────────────
class _ChartCanvas(FigureCanvas):
    def __init__(self) -> None:
        self._fig = Figure(figsize=(12, 8), tight_layout=True)
        super().__init__(self._fig)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._ax_equity, self._ax_daily = self._fig.subplots(2, 1)
        self._ax_equity.set_title(t("equity_curve"))
        self._ax_daily.set_title(t("daily_pnl"))
        for ax in (self._ax_equity, self._ax_daily):
            _apply_dark_style(self._fig, ax)

    def plot(self, rows: list) -> None:
        """rows: list of dicts {date:str, pnl:float, commission:float}"""
        self._ax_equity.clear()
        self._ax_daily.clear()
        self._ax_equity.set_title(t("equity_curve"))
        self._ax_daily.set_title(t("daily_pnl"))
        for ax in (self._ax_equity, self._ax_daily):
            _apply_dark_style(self._fig, ax)

        if not rows:
            for ax in (self._ax_equity, self._ax_daily):
                ax.text(
                    0.5, 0.5, t("no_data"),
                    ha="center", va="center",
                    color=_TEXT, transform=ax.transAxes, fontsize=13,
                )
            self.draw()
            return

        dates = [datetime.date.fromisoformat(str(r["date"])) for r in rows]
        daily_pnl = [float(r["pnl"] or 0) - float(r["commission"] or 0) for r in rows]
        cumulative = []
        running = 0.0
        for v in daily_pnl:
            running += v
            cumulative.append(running)

        # ── Equity curve ──────────────────────────────────────────────
        self._ax_equity.plot(dates, cumulative, color=_BLUE, linewidth=1.5)
        self._ax_equity.fill_between(
            dates, cumulative, 0,
            where=[v >= 0 for v in cumulative],
            alpha=0.15, color=_GREEN,
        )
        self._ax_equity.fill_between(
            dates, cumulative, 0,
            where=[v < 0 for v in cumulative],
            alpha=0.15, color=_RED,
        )
        self._ax_equity.axhline(0, color=_GRID, linewidth=0.8)
        self._ax_equity.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        self._ax_equity.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._ax_equity.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.2f}")
        )
        self._fig.autofmt_xdate(ax=self._ax_equity, rotation=30, ha="right")

        # ── Daily P&L bars ────────────────────────────────────────────
        colors = [_GREEN if v >= 0 else _RED for v in daily_pnl]
        self._ax_daily.bar(dates, daily_pnl, color=colors, width=0.6)
        self._ax_daily.axhline(0, color=_GRID, linewidth=0.8)
        self._ax_daily.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        self._ax_daily.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._ax_daily.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.2f}")
        )
        self._fig.autofmt_xdate(ax=self._ax_daily, rotation=30, ha="right")

        self._fig.tight_layout(pad=2.0)
        self.draw()


# ── Main widget ──────────────────────────────────────────────────────────────
class ProfileWidget(QWidget):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._users: list[dict] = []   # admin only
        self._setup_ui()
        self._load_users()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ── Top bar ────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.setSpacing(8)

        if self._session.is_admin:
            top.addWidget(QLabel(t("select_user") + ":"))
            self._user_combo = QComboBox()
            self._user_combo.setMinimumWidth(180)
            self._user_combo.setStyleSheet(
                "QComboBox { background:#161b22; color:#e6edf3;"
                " border:1px solid #30363d; border-radius:4px; padding:3px 8px; }"
                "QComboBox::drop-down { border:none; }"
                "QComboBox QAbstractItemView { background:#1c2128; color:#e6edf3;"
                " selection-background-color:#388bfd; }"
            )
            self._user_combo.currentIndexChanged.connect(self._on_user_changed)
            top.addWidget(self._user_combo)
        else:
            self._user_combo = None

        top.addStretch()
        btn_refresh = QPushButton(t("refresh"))
        btn_refresh.setObjectName("primarySmBtn")
        btn_refresh.clicked.connect(self.refresh)
        top.addWidget(btn_refresh)
        root.addLayout(top)

        # ── Stats row ─────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self._card_pnl   = _StatCard(t("total_pnl"))
        self._card_wr    = _StatCard(t("win_rate"))
        self._card_cnt   = _StatCard(t("total_trades"))
        self._card_comm  = _StatCard(t("total_commission"))
        for card in (self._card_pnl, self._card_wr, self._card_cnt, self._card_comm):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            stats_row.addWidget(card)
        root.addLayout(stats_row)

        # ── Charts ────────────────────────────────────────────────────
        self._canvas = _ChartCanvas()
        root.addWidget(self._canvas)

    # ── Data loading ──────────────────────────────────────────────────────────
    def _load_users(self) -> None:
        """Populate the user combo (admin only) then trigger first chart load."""
        if self._session.is_admin:
            self._users = db.get_all_users()
            self._user_combo.blockSignals(True)
            self._user_combo.clear()
            for u in self._users:
                self._user_combo.addItem(u["username"])
            self._user_combo.blockSignals(False)

        self.refresh()

    def _current_user_id(self) -> Optional[int]:
        if self._session.is_admin:
            idx = self._user_combo.currentIndex()
            if idx < 0 or idx >= len(self._users):
                return None
            return self._users[idx]["id"]
        return self._session.user_id

    def _on_user_changed(self, _index: int) -> None:
        self.refresh()

    def refresh(self) -> None:
        uid = self._current_user_id()
        if uid is None:
            self._canvas.plot([])
            self._update_stats([])
            return

        rows = db.get_daily_pnl(uid)
        self._canvas.plot(rows)
        self._update_stats(rows)

    def _update_stats(self, rows: list) -> None:
        if not rows:
            for card in (self._card_pnl, self._card_wr, self._card_cnt, self._card_comm):
                card.set_value("--")
            return

        daily_net = [float(r["pnl"] or 0) - float(r["commission"] or 0) for r in rows]
        total_pnl = sum(daily_net)
        win_days  = sum(1 for v in daily_net if v > 0)
        win_rate  = win_days / len(daily_net) * 100 if daily_net else 0
        trade_rows = db.get_order_history(user_id=self._current_user_id(), limit=10000)
        total_trades = len(trade_rows)
        total_comm   = sum(float(r["commission"] or 0) for r in rows)

        pnl_color = _GREEN if total_pnl >= 0 else _RED
        self._card_pnl.set_value(f"{total_pnl:+,.4f}", color=pnl_color)
        self._card_wr.set_value(f"{win_rate:.1f}%")
        self._card_cnt.set_value(str(total_trades))
        self._card_comm.set_value(f"{total_comm:,.4f}")

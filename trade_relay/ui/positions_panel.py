"""
Bottom positions/orders panel with 4 tabs:
  仓位 | 当前委托 | 历史委托 | 历史成交
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from trade_relay.i18n import t
from trade_relay import database as db
from trade_relay.auth.manager import Session


# ── Color palettes ────────────────────────────────────────────────────────────

SIDE_COLORS = {
    "BUY":   "#26a69a",
    "SELL":  "#ef5350",
    "LONG":  "#26a69a",
    "SHORT": "#ef5350",
    "BOTH":  "#e6edf3",
}
STATUS_COLORS = {
    "FILLED":           "#56d364",
    "NEW":              "#58a6ff",
    "PARTIALLY_FILLED": "#f0883e",
    "PENDING_CANCEL":   "#f0883e",
    "PENDING":          "#f0883e",
    "CANCELED":         "#8b949e",
    "EXPIRED":          "#8b949e",
    "FAILED":           "#f85149",
    "ERROR":            "#f85149",
    "REJECTED":         "#f85149",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_table(headers: list[tuple[str, int]]) -> QTableWidget:
    tbl = QTableWidget()
    tbl.setColumnCount(len(headers))
    tbl.setHorizontalHeaderLabels([h for h, _ in headers])
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.verticalHeader().setVisible(False)
    tbl.setShowGrid(True)
    tbl.setSortingEnabled(False)
    tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    tbl.setAlternatingRowColors(False)
    tbl.verticalHeader().setDefaultSectionSize(24)
    tbl.setStyleSheet(
        "QTableWidget { background:#0d1117; gridline-color:#21262d; }"
        "QTableWidget::item { padding: 0 6px; }"
        "QTableWidget::item:selected { background:#1c2128; }"
        "QHeaderView::section { background:#161b22; color:#8b949e;"
        "  border:none; border-bottom:1px solid #30363d;"
        "  padding:4px 6px; font-size:12px; }"
    )
    hdr = tbl.horizontalHeader()
    for i, (_, width) in enumerate(headers):
        if width < 0:
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            tbl.setColumnWidth(i, width)
    return tbl


def _set(tbl: QTableWidget, row: int, col: int, text: str,
         color: str | None = None,
         align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter) -> None:
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    tbl.setItem(row, col, item)


# ── Tab strip ─────────────────────────────────────────────────────────────────

class _TabStrip(QWidget):
    def __init__(self, labels: list[str]) -> None:
        super().__init__()
        self.setFixedHeight(34)
        lo = QHBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns: list[QPushButton] = []
        self._base_labels: list[str] = list(labels)

        for i, lbl in enumerate(labels):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setObjectName("pos_tab_btn")
            btn.setMinimumWidth(110)
            btn.setFixedHeight(34)
            self._group.addButton(btn, i)
            lo.addWidget(btn)
            self._btns.append(btn)

        lo.addStretch()

    def set_count(self, tab_idx: int, count: int) -> None:
        base = self._base_labels[tab_idx]
        btn = self._btns[tab_idx]
        btn.setText(f"{base}({count})" if count > 0 else base)

    @property
    def tab_selected(self):
        return self._group.idClicked


# ── Main panel widget ─────────────────────────────────────────────────────────

class PositionsPanel(QWidget):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._cur_tab = 0
        self._setup_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_current)
        self._timer.start(5_000)
        self._refresh_all()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        # separator line at top
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#21262d;")
        lo.addWidget(sep)

        # tab strip
        tab_labels = [
            t("tab_positions"),
            t("tab_open_orders"),
            t("tab_order_history"),
            t("tab_trade_history"),
        ]
        self._tabs = _TabStrip(tab_labels)
        self._tabs.tab_selected.connect(self._on_tab)
        lo.addWidget(self._tabs)

        # table stack
        self._stack = QStackedWidget()
        lo.addWidget(self._stack, 1)

        # ── Tab 0: Positions ──────────────────────────────────────────────────
        pos_cols = [
            (t("pos_contract"),    120),
            (t("pos_side"),         70),
            (t("pos_size"),         90),
            (t("pos_entry_price"), 110),
            (t("pos_liq_price"),   110),
            (t("pos_pnl"),         140),
            (t("pos_leverage"),     65),
            (t("pos_margin_type"),  80),
            (t("pos_margin"),      -1),
        ]
        self._pos_tbl = _make_table(pos_cols)
        self._stack.addWidget(self._pos_tbl)

        # ── Tab 1: Open Orders ────────────────────────────────────────────────
        open_cols = [
            (t("col_time"),        155),
            (t("col_user"),         80),
            (t("col_symbol"),       90),
            (t("col_side"),         60),
            (t("col_type"),         80),
            (t("col_qty"),          90),
            (t("col_price"),       100),
            (t("col_status"),       90),
            (t("col_order_id"),    -1),
        ]
        self._open_tbl = _make_table(open_cols)
        self._stack.addWidget(self._open_tbl)

        # ── Tab 2: Order History ──────────────────────────────────────────────
        hist_cols = [
            (t("col_time"),        155),
            (t("col_user"),         80),
            (t("col_symbol"),       90),
            (t("col_side"),         60),
            (t("col_type"),         80),
            (t("col_qty"),          90),
            (t("col_price"),       100),
            (t("col_status"),       90),
            (t("col_order_id"),    -1),
        ]
        self._hist_tbl = _make_table(hist_cols)
        self._stack.addWidget(self._hist_tbl)

        # ── Tab 3: Trade History (FILLED only) ────────────────────────────────
        trade_cols = [
            (t("col_time"),        155),
            (t("col_user"),         80),
            (t("col_symbol"),       90),
            (t("col_side"),         60),
            (t("col_type"),         80),
            (t("col_filled_qty"),   90),
            (t("col_avg_price"),   110),
            (t("col_order_id"),    -1),
        ]
        self._trade_tbl = _make_table(trade_cols)
        self._stack.addWidget(self._trade_tbl)

        self._stack.setCurrentIndex(0)

    # ── Slot / public ─────────────────────────────────────────────────────────

    def _on_tab(self, idx: int) -> None:
        self._cur_tab = idx
        self._stack.setCurrentIndex(idx)
        self._refresh_current()

    def _refresh_current(self) -> None:
        [
            self._load_positions,
            self._load_open_orders,
            self._load_order_history,
            self._load_trade_history,
        ][self._cur_tab]()

    def _refresh_all(self) -> None:
        self._load_positions()
        self._load_open_orders()
        self._load_order_history()
        self._load_trade_history()

    def refresh(self) -> None:
        """Call after placing an order to update all tabs."""
        self._refresh_all()

    # ── Data loaders ──────────────────────────────────────────────────────────

    def _uid(self) -> int | None:
        return None if self._session.is_admin else self._session.user_id

    def _load_positions(self) -> None:
        try:
            rows = db.get_positions(user_id=self._uid())
        except Exception:
            rows = []

        tbl = self._pos_tbl
        tbl.setRowCount(len(rows))
        self._tabs.set_count(0, len(rows))

        for r, pos in enumerate(rows):
            qty       = pos.get("quantity") or 0
            entry     = pos.get("avg_entry_price")
            pnl       = pos.get("unrealized_pnl")
            liq       = pos.get("liq_price") or pos.get("liquidation_price")
            margin    = pos.get("margin") or pos.get("realized_pnl")
            side      = pos.get("position_side", "BOTH")
            side_c    = SIDE_COLORS.get(side, "#e6edf3")
            pnl_c     = "#26a69a" if float(pnl or 0) >= 0 else "#ef5350"

            _set(tbl, r, 0, pos.get("symbol", ""), "#e6edf3", Qt.AlignmentFlag.AlignLeft)
            _set(tbl, r, 1, side, side_c)
            _set(tbl, r, 2, f"{float(qty):,.4f}", "#e6edf3")
            _set(tbl, r, 3, f"{float(entry):,.2f}" if entry else "--", "#e6edf3")
            _set(tbl, r, 4, f"{float(liq):,.2f}" if liq else "--", "#f0883e")
            _set(tbl, r, 5,
                 f"{float(pnl):+,.2f}" if pnl is not None else "--", pnl_c)
            _set(tbl, r, 6, f"{pos.get('leverage', 1)}x", "#e6edf3")
            _set(tbl, r, 7, pos.get("margin_type", "--"), "#8b949e")
            _set(tbl, r, 8,
                 f"{float(margin):,.2f}" if margin is not None else "--", "#e6edf3")

    def _fill_order_rows(self, tbl: QTableWidget, rows: list,
                         tab_idx: int, is_trade: bool = False) -> None:
        tbl.setRowCount(len(rows))
        self._tabs.set_count(tab_idx, len(rows))

        for r, o in enumerate(rows):
            dt     = o.get("created_at")
            dt_s   = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "--"
            side   = o.get("side", "")
            sid_c  = SIDE_COLORS.get(side, "#e6edf3")
            status = o.get("status", "")
            sta_c  = STATUS_COLORS.get(status, "#e6edf3")
            oid    = str(o.get("exchange_order_id") or o.get("binance_order_id") or "--")

            _set(tbl, r, 0, dt_s,                   "#8b949e", Qt.AlignmentFlag.AlignLeft)
            _set(tbl, r, 1, o.get("username", ""),   "#e6edf3")
            _set(tbl, r, 2, o.get("symbol", ""),     "#e6edf3")
            _set(tbl, r, 3, side,                    sid_c)
            _set(tbl, r, 4, o.get("order_type", ""), "#8b949e")

            if is_trade:
                filled = o.get("filled_qty") or 0
                avg    = o.get("avg_price")
                _set(tbl, r, 5, f"{float(filled):,.4f}",                   "#e6edf3")
                _set(tbl, r, 6, f"{float(avg):,.2f}" if avg else "--",      "#e6edf3")
                _set(tbl, r, 7, oid, "#8b949e", Qt.AlignmentFlag.AlignLeft)
            else:
                qty   = o.get("quantity") or 0
                price = o.get("price")
                _set(tbl, r, 5, f"{float(qty):,.4f}",                        "#e6edf3")
                _set(tbl, r, 6,
                     f"{float(price):,.2f}" if price else t("market_placeholder"),
                     "#e6edf3")
                _set(tbl, r, 7, status, sta_c)
                _set(tbl, r, 8, oid, "#8b949e", Qt.AlignmentFlag.AlignLeft)

    def _load_open_orders(self) -> None:
        try:
            rows = db.get_active_orders(user_id=self._uid())
        except Exception:
            rows = []
        self._fill_order_rows(self._open_tbl, rows, 1)

    def _load_order_history(self) -> None:
        try:
            rows = db.get_order_history(user_id=self._uid())
        except Exception:
            rows = []
        self._fill_order_rows(self._hist_tbl, rows, 2)

    def _load_trade_history(self) -> None:
        try:
            rows = db.get_order_history(user_id=self._uid())
            rows = [o for o in rows if o.get("status") == "FILLED"]
        except Exception:
            rows = []
        self._fill_order_rows(self._trade_tbl, rows, 3, is_trade=True)

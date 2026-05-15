"""
Order form widget – Binance futures-style two-panel layout.
  Left:  live order book (asks / bids) pushed via Binance Futures WebSocket
  Right: trading form  (open/close · limit/market · long/short)
"""
import asyncio
import json
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QListView,
    QCheckBox, QSlider, QSplitter, QFrame,
    QButtonGroup, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer

from trade_relay.i18n import t
from trade_relay.auth.manager import Session
from trade_relay.trading.order_manager import submit_order
from trade_relay import database as db


# ─────────────────────────── Background workers ──────────────────────────────

class _OrderWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, session: Session, symbol: str, side: str,
                 order_type: str, quantity: float, price, stop_price=None) -> None:
        super().__init__()
        self._session    = session
        self._symbol     = symbol
        self._side       = side
        self._order_type = order_type
        self._quantity   = quantity
        self._price      = price
        self._stop_price = stop_price

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            r = loop.run_until_complete(
                submit_order(self._session, self._symbol, self._side,
                             self._order_type, self._quantity, self._price,
                             self._stop_price)
            )
            self.finished.emit(r.success, r.message)
        except Exception as exc:
            self.finished.emit(False, str(exc))
        finally:
            loop.close()


class _ClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            text = self.text().replace(",", "").strip()
            if text and text != "--":
                self.clicked.emit(text)
        super().mousePressEvent(event)


class _BookWsWorker(QThread):
    """Streams order book depth + last price via Binance Futures WebSocket."""
    ready = pyqtSignal(list, list, str)   # asks, bids, last_price

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self._symbol = symbol.lower()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    def stop(self) -> None:
        """Signal the worker to stop from any thread."""
        if self._loop and not self._loop.is_closed() and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._stop_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._stream())
        finally:
            self._loop.close()

    async def _stream(self) -> None:
        import websockets  # noqa: PLC0415
        sym = self._symbol
        url = (
            f"wss://fstream.binance.com/stream?streams="
            f"{sym}@depth10@500ms/{sym}@miniTicker"
        )
        asks: list = []
        bids: list  = []
        last: str   = ""
        stop = self._stop_event
        while not stop.is_set():
            try:
                async with websockets.connect(url) as ws:
                    while not stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        data    = json.loads(msg)
                        stream  = data.get("stream", "")
                        payload = data.get("data", {})
                        if "depth" in stream:
                            asks = payload.get("a", [])
                            bids = payload.get("b", [])
                        elif "miniTicker" in stream:
                            last = payload.get("c", last)
                        self.ready.emit(asks, bids, last)
            except Exception:
                if stop.is_set():
                    return
                # Wait up to 2 s before reconnecting
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


class _PlatformTradesWorker(QThread):
    """Polls the local DB for filled orders across all platform users."""
    refreshed = pyqtSignal(list)   # list of dicts from get_recent_platform_trades

    INTERVAL_MS = 5_000

    def __init__(self) -> None:
        super().__init__()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            try:
                rows = db.get_recent_platform_trades(limit=30)
                self.refreshed.emit(list(rows))
            except Exception:
                pass
            # sleep in 500 ms chunks so stop() is noticed quickly
            for _ in range(self.INTERVAL_MS // 500):
                if not self._running:
                    return
                time.sleep(0.5)


# ─────────────────────────── Helpers ─────────────────────────────────────────

def _fmt(val: float) -> str:
    """Format large numbers as e.g. 23.45K or 1.23M."""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"{val / 1_000:.2f}K"
    return f"{val:,.2f}"


_KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "TUSD", "BTC", "ETH", "BNB")


def _parse_symbol(sym: str) -> tuple[str, str]:
    """Split e.g. 'BTCUSDT' → ('BTC', 'USDT'). Falls back to ('', sym)."""
    sym = sym.upper()
    for q in _KNOWN_QUOTES:
        if sym.endswith(q) and len(sym) > len(q):
            return sym[: -len(q)], q
    return "", sym


class _TabBar(QWidget):
    """Row of checkable QPushButtons that act as a tab strip."""
    currentChanged = pyqtSignal(int)

    def __init__(self, labels: list, obj_name: str) -> None:
        super().__init__()
        lo = QHBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns = []

        for i, lbl in enumerate(labels):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setObjectName(obj_name)
            self._group.addButton(btn, i)
            lo.addWidget(btn)
            self._btns.append(btn)

        self._group.idClicked.connect(self.currentChanged)

    def current_index(self) -> int:
        return self._group.checkedId()


def _configure_combo_popup(combo: QComboBox) -> None:
    """Use a list view with mouse tracking so hover/selection feedback is immediate."""
    view = QListView(combo)
    view.setMouseTracking(True)
    view.viewport().setMouseTracking(True)
    combo.setView(view)


_MAX_RECENT_TRADES = 20


# ─────────────────────────── Recent Trades panel (platform) ──────────────────

_COLS = [
    ("rt_user_col",   Qt.AlignmentFlag.AlignLeft,  0),
    ("rt_symbol_col", Qt.AlignmentFlag.AlignLeft,  0),
    ("rt_side_col",   Qt.AlignmentFlag.AlignLeft,  0),
    ("rt_qty_col",    Qt.AlignmentFlag.AlignRight, 0),
    ("rt_value_col",  Qt.AlignmentFlag.AlignRight, 0),
    ("rt_time_col",   Qt.AlignmentFlag.AlignRight, 0),
]


class _RecentTradesPanel(QWidget):
    """Shows recent filled orders placed through the Trade-Relay platform."""

    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(0)

        # ── title ──
        title = QLabel(t("recent_trades"))
        title.setStyleSheet(
            "color: #e6edf3; font-weight: bold; font-size: 12px; padding: 2px 0;"
        )
        outer.addWidget(title)
        outer.addSpacing(4)

        # ── column headers ──
        col_hdr = QHBoxLayout()
        col_hdr.setContentsMargins(2, 0, 2, 2)
        col_hdr.setSpacing(4)
        for key, align, _ in _COLS:
            lbl = QLabel(t(key))
            lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
            lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            col_hdr.addWidget(lbl, 1)
        outer.addLayout(col_hdr)

        # ── scrollable rows ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        rows_lo = QVBoxLayout(content)
        rows_lo.setContentsMargins(0, 0, 0, 0)
        rows_lo.setSpacing(0)

        self._rows: list[list[QLabel]] = []
        for _ in range(_MAX_RECENT_TRADES):
            row_w = QWidget()
            row_w.setFixedHeight(20)
            h = QHBoxLayout(row_w)
            h.setContentsMargins(2, 0, 2, 0)
            h.setSpacing(4)
            cells: list[QLabel] = []
            for _, align, _ in _COLS:
                lbl = QLabel("--")
                lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
                lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                h.addWidget(lbl, 1)
                cells.append(lbl)
            rows_lo.addWidget(row_w)
            self._rows.append(cells)

        rows_lo.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    def refresh(self, rows: list) -> None:
        """Called by _PlatformTradesWorker with latest DB rows."""
        for i, cells in enumerate(self._rows):
            if i < len(rows):
                r = rows[i]
                username   = str(r.get("username", "--"))
                symbol     = str(r.get("symbol", "--"))
                side       = str(r.get("side", "--"))
                filled_qty = r.get("filled_qty") or 0
                avg_price  = r.get("avg_price") or 0
                created_at = r.get("created_at")

                try:
                    value = float(filled_qty) * float(avg_price)
                    qty_str = f"{float(filled_qty):,.4f}"
                    val_str = _fmt(value)
                except (TypeError, ValueError):
                    qty_str = "--"
                    val_str = "--"

                if created_at:
                    try:
                        ts = created_at.strftime("%H:%M:%S")
                    except AttributeError:
                        ts = str(created_at)[-8:]
                else:
                    ts = "--"

                side_color = "#3fb950" if side == "BUY" else "#f85149"
                cells[0].setStyleSheet("color: #e6edf3; font-size: 11px;")
                cells[1].setStyleSheet("color: #e6edf3; font-size: 11px;")
                cells[2].setStyleSheet(f"color: {side_color}; font-size: 11px;")
                cells[3].setStyleSheet("color: #e6edf3; font-size: 11px;")
                cells[4].setStyleSheet("color: #e6edf3; font-size: 11px;")
                cells[5].setStyleSheet("color: #8b949e; font-size: 11px;")

                cells[0].setText(username)
                cells[1].setText(symbol)
                cells[2].setText(side)
                cells[3].setText(qty_str)
                cells[4].setText(val_str)
                cells[5].setText(ts)
            else:
                for cell in cells:
                    cell.setStyleSheet("color: #8b949e; font-size: 11px;")
                    cell.setText("--")


# ─────────────────────────── Account Info panel ───────────────────────────────

class _AccountPanel(QWidget):
    """Account info panel – mirrors Binance's margin-ratio card layout."""

    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _key_lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("color: #8b949e; font-size: 12px;")
        return l

    @staticmethod
    def _val_lbl(text: str = "--") -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("color: #e6edf3; font-size: 12px;")
        l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return l

    @staticmethod
    def _kv_row(key_lbl: QLabel, val_lbl: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(key_lbl, 1)
        row.addWidget(val_lbl, 1)
        return row

    def _setup_ui(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(12, 8, 12, 10)
        lo.setSpacing(0)

        # ── title row: "账户"  ···  "切换" ──
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 6)
        title_lbl = QLabel(t("acct_title"))
        title_lbl.setStyleSheet(
            "color: #e6edf3; font-size: 13px; font-weight: bold;"
        )
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        lo.addLayout(title_row)

        # ── top separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #30363d; min-height: 1px; max-height: 1px;")
        lo.addWidget(sep)
        lo.addSpacing(8)

        # ── section header: "保证金比率" ──
        section_lbl = QLabel(t("acct_margin_ratio"))
        section_lbl.setStyleSheet(
            "color: #8b949e; font-size: 11px; font-weight: bold;"
        )
        lo.addWidget(section_lbl)
        lo.addSpacing(6)

        # ── data rows ──
        rows = [
            ("acct_risk_rate",       "#f0883e"),   # amber – risk rate
            ("acct_maint_margin",    None),
            ("acct_total_equity",    None),
            ("acct_pos_value",       None),
            ("acct_actual_leverage", None),
            ("acct_pnl",             None),
            ("acct_wallet_balance",  "#3fb950"),   # green – wallet
        ]
        self._val_labels: dict[str, QLabel] = {}
        for key, val_color in rows:
            val = self._val_lbl()
            if val_color:
                val.setStyleSheet(
                    f"color: {val_color}; font-size: 12px;"
                )
            self._val_labels[key] = val
            lo.addLayout(self._kv_row(self._key_lbl(t(key)), val))
            lo.addSpacing(10)

        lo.addStretch(1)

    def update_account(self, equity: str, margin: str, avail: str, pnl: str,
                       risk_rate: str = "--", maint_margin: str = "--",
                       pos_value: str = "--", actual_leverage: str = "--",
                       wallet_balance: str = "--") -> None:
        def _set(key: str, text: str) -> None:
            lbl = self._val_labels.get(key)
            if lbl:
                lbl.setText(text)

        _set("acct_risk_rate",       risk_rate)
        _set("acct_maint_margin",    f"{maint_margin} USDC")
        _set("acct_total_equity",    f"{equity} USDC")
        _set("acct_pos_value",       f"{pos_value} USDC")
        _set("acct_actual_leverage", actual_leverage)
        _set("acct_pnl",             f"{pnl} USDC")
        _set("acct_wallet_balance",  f"{wallet_balance} USDC")


# ─────────────────────────── Order Book panel ────────────────────────────────

class _OrderBookPanel(QWidget):
    price_clicked = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(240)
        self._setup_ui()
        self._connect_price_clicks()

    def _setup_ui(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(8, 8, 8, 8)
        lo.setSpacing(0)

        # ── header ──
        hdr = QHBoxLayout()
        title = QLabel(t("order_book"))
        title.setStyleSheet(
            "color: #e6edf3; font-weight: bold; font-size: 13px;"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        lo.addLayout(hdr)
        lo.addSpacing(6)

        # ── column labels ──
        col_hdr = QHBoxLayout()
        col_hdr.setContentsMargins(2, 0, 2, 2)
        self._col_price = QLabel("Price")
        self._col_price.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._col_size = QLabel("Size")
        self._col_size.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._col_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._col_sum = QLabel("Sum")
        self._col_sum.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._col_sum.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        col_hdr.addWidget(self._col_price, 1)
        col_hdr.addWidget(self._col_size, 1)
        col_hdr.addWidget(self._col_sum, 1)
        lo.addLayout(col_hdr)

        # ── ask rows (red) – displayed highest→lowest ──
        self._ask_rows = []
        for _ in range(8):
            row_w, cells = self._make_row("#f85149")
            self._ask_rows.append(cells)
            lo.addWidget(row_w)

        lo.addSpacing(2)

        # ── mid price display ──
        mid = QHBoxLayout()
        self._price_lbl = _ClickableLabel("--")
        self._price_lbl.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
            " color: #3fb950; padding: 4px 2px;"
        )
        self._price_lbl.clicked.connect(self.price_clicked)
        self._price_ref = QLabel("--")
        self._price_ref.setStyleSheet("font-size: 12px; color: #8b949e;")
        self._price_ref.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        mid.addWidget(self._price_lbl)
        mid.addStretch()
        mid.addWidget(self._price_ref)
        lo.addLayout(mid)
        lo.addSpacing(2)

        # ── bid rows (green) – displayed highest→lowest ──
        self._bid_rows = []
        for _ in range(8):
            row_w, cells = self._make_row("#3fb950")
            self._bid_rows.append(cells)
            lo.addWidget(row_w)

        lo.addStretch()

    def set_symbol(self, base: str, quote: str) -> None:
        self._col_price.setText(f"Price ({quote})")
        self._col_size.setText(f"Size ({base})")
        self._col_sum.setText(f"Sum ({base})")

    @staticmethod
    def _make_row(price_color: str) -> tuple:
        w = QWidget()
        w.setFixedHeight(21)
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(0)

        p = _ClickableLabel("--")
        p.setStyleSheet(f"color: {price_color}; font-size: 12px;")
        p.setFixedWidth(84)

        q = QLabel("--")
        q.setStyleSheet("color: #e6edf3; font-size: 12px;")
        q.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        s = QLabel("--")
        s.setStyleSheet("color: #8b949e; font-size: 12px;")
        s.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        h.addWidget(p)
        h.addWidget(q, 1)
        h.addSpacing(4)
        h.addWidget(s, 1)
        return w, (p, q, s)

    def _connect_price_clicks(self) -> None:
        for p, _, _ in self._ask_rows + self._bid_rows:
            p.clicked.connect(self.price_clicked)

    def update_book(self, asks: list, bids: list, last: str) -> None:
        # Binance returns asks lowest-first; display highest-first
        disp_asks = list(reversed(asks))
        for i, (p, q, s) in enumerate(self._ask_rows):
            if i < len(disp_asks):
                price = float(disp_asks[i][0])
                qty   = float(disp_asks[i][1])
                cum   = sum(float(disp_asks[j][1]) for j in range(i + 1))
                p.setText(f"{price:,.1f}")
                q.setText(_fmt(qty))
                s.setText(_fmt(cum))
            else:
                p.setText("--"); q.setText("--"); s.setText("--")

        for i, (p, q, s) in enumerate(self._bid_rows):
            if i < len(bids):
                price = float(bids[i][0])
                qty   = float(bids[i][1])
                cum   = sum(float(bids[j][1]) for j in range(i + 1))
                p.setText(f"{price:,.1f}")
                q.setText(_fmt(qty))
                s.setText(_fmt(cum))
            else:
                p.setText("--"); q.setText("--"); s.setText("--")

        try:
            text = f"{float(last):,.1f}"
        except (ValueError, TypeError):
            text = last or "--"
        self._price_lbl.setText(text)
        self._price_ref.setText(text)


# ─────────────────────────── Trading form panel ──────────────────────────────

class _OrderFormPanel(QWidget):
    long_clicked  = pyqtSignal()
    short_clicked = pyqtSignal()

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._setup_ui()

    def _setup_ui(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(12, 10, 12, 10)
        lo.setSpacing(6)

        # ── margin mode / leverage tags ──
        top = QHBoxLayout()
        top.setSpacing(4)
        for txt in [t("margin_cross"), "100x", "M"]:
            b = QPushButton(txt)
            b.setObjectName("tag_btn")
            b.setFixedHeight(24)
            top.addWidget(b)
        top.addStretch()
        more = QPushButton("···")
        more.setObjectName("tag_btn")
        more.setFixedHeight(24)
        top.addWidget(more)
        lo.addLayout(top)

        # ── Open / Close direction tabs ──
        self._dir_bar = _TabBar([t("open_position"), t("close_position")], "tab_dir")
        lo.addWidget(self._dir_bar)

        # ── Order type tabs ──
        self._type_bar = _TabBar([t("limit_tab"), t("market_tab"), t("conditional_tab")], "tab_type")
        self._type_bar.currentChanged.connect(self._on_type_changed)
        lo.addWidget(self._type_bar)

        # ── Available balance ──
        avail_row = QHBoxLayout()
        avail_row.setContentsMargins(0, 2, 0, 0)
        lbl_avail = QLabel(t("available_balance"))
        lbl_avail.setStyleSheet("color: #8b949e; font-size: 12px;")
        self._avail_val = QLabel("-- USDC")
        self._avail_val.setStyleSheet("color: #e6edf3; font-size: 12px;")
        avail_row.addWidget(lbl_avail)
        avail_row.addSpacing(6)
        avail_row.addWidget(self._avail_val)
        avail_row.addStretch()
        lo.addLayout(avail_row)

        # ── Price (委托价格) ──
        lo.addWidget(_small_label(t("order_price_label")))
        price_row = QHBoxLayout()
        price_row.setSpacing(6)

        price_container = QWidget()
        price_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        price_container.setStyleSheet(
            "QWidget { background:#21262d; border:1px solid #30363d;"
            " border-radius:6px; }"
            "QWidget:focus-within { border-color:#58a6ff; }"
        )
        price_container.setMinimumHeight(42)
        price_c_lo = QHBoxLayout(price_container)
        price_c_lo.setContentsMargins(12, 0, 12, 0)
        price_c_lo.setSpacing(6)
        self._price_edit = QLineEdit()
        self._price_edit.setPlaceholderText("--")
        self._price_edit.setFrame(False)
        self._price_edit.setStyleSheet(
            "QLineEdit { background:transparent; color:#e6edf3;"
            " font-size:16px; border:none; }"
        )
        self._quote_lbl = QLabel("USDT")
        self._quote_lbl.setStyleSheet(
            "color:#e6edf3; font-size:13px; min-width:40px; border:none; background:transparent;"
        )
        self._quote_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        price_c_lo.addWidget(self._price_edit, 1)
        price_c_lo.addWidget(self._quote_lbl)

        bbo_btn = QPushButton("BBO")
        bbo_btn.setObjectName("tag_btn")
        bbo_btn.setFixedHeight(42)
        bbo_btn.setFixedWidth(52)
        price_row.addWidget(price_container, 1)
        price_row.addWidget(bbo_btn)
        lo.addLayout(price_row)

        self._trigger_wrap = QWidget()
        trigger_lo = QVBoxLayout(self._trigger_wrap)
        trigger_lo.setContentsMargins(0, 0, 0, 0)
        trigger_lo.setSpacing(4)
        trigger_lo.addWidget(_small_label(t("trigger_price_label")))

        trigger_row = QHBoxLayout()
        trigger_row.setSpacing(4)
        self._trigger_edit = QLineEdit()
        self._trigger_edit.setPlaceholderText("--")
        self._trigger_edit.setObjectName("trade_input")
        self._trigger_edit.setMinimumHeight(34)
        self._trigger_quote_lbl = QLabel("USDT")
        self._trigger_quote_lbl.setStyleSheet(
            "color:#8b949e; font-size:12px; min-width:48px;"
        )
        self._trigger_quote_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        trigger_row.addWidget(self._trigger_edit, 1)
        trigger_row.addWidget(self._trigger_quote_lbl)
        trigger_lo.addLayout(trigger_row)
        self._trigger_wrap.setVisible(False)
        lo.addWidget(self._trigger_wrap)

        # ── Quantity (数量) ──
        lo.addWidget(_small_label(t("order_qty_label")))
        qty_row = QHBoxLayout()
        qty_row.setSpacing(4)
        self._qty_edit = QLineEdit()
        self._qty_edit.setPlaceholderText("0")
        self._qty_edit.setObjectName("trade_input")
        self._qty_edit.setMinimumHeight(34)
        self._unit_combo = QComboBox()
        self._unit_combo.addItems(["USDC", "Cont"])
        _configure_combo_popup(self._unit_combo)
        self._unit_combo.setMinimumWidth(92)
        self._unit_combo.setMinimumHeight(34)
        qty_row.addWidget(self._qty_edit, 9)
        qty_row.addWidget(self._unit_combo, 1)
        lo.addLayout(qty_row)

        # ── Percentage slider ──
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setSingleStep(25)
        self._slider.setPageStep(25)
        self._slider.setObjectName("pct_slider")
        lo.addWidget(self._slider)

        tick_row = QHBoxLayout()
        tick_row.setContentsMargins(0, 0, 0, 0)
        tick_row.setSpacing(0)
        for i, txt in enumerate(["0%", "25%", "50%", "75%", "100%"]):
            lbl = QLabel(txt)
            lbl.setStyleSheet("color: #484f58; font-size: 10px;")
            if i == 0:
                lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
            elif i == 4:
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            else:
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tick_row.addWidget(lbl, 1)
        lo.addLayout(tick_row)

        # ── Stop profit / loss ──
        tpsl_hdr = QHBoxLayout()
        tpsl_hdr.setContentsMargins(0, 2, 0, 0)
        self._tpsl = QCheckBox(t("take_profit_stop_loss"))
        self._tpsl.setStyleSheet("color: #8b949e; font-size: 12px; spacing: 6px;")
        tpsl_hdr.addWidget(self._tpsl)
        tpsl_hdr.addStretch()
        adv_lbl = QLabel(t("tpsl_advanced"))
        adv_lbl.setStyleSheet("color: #f0883e; font-size: 12px;")
        tpsl_hdr.addWidget(adv_lbl)
        lo.addLayout(tpsl_hdr)

        # Collapsible TP/SL panel
        self._tpsl_panel = QWidget()
        self._tpsl_panel.setVisible(False)
        tpsl_lo = QVBoxLayout(self._tpsl_panel)
        tpsl_lo.setContentsMargins(0, 4, 0, 0)
        tpsl_lo.setSpacing(6)

        def _tpsl_row(label_key: str):
            hdr = QHBoxLayout()
            hdr.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(t(label_key))
            lbl.setStyleSheet("color: #8b949e; font-size: 12px;")
            latest = QLabel(t("tpsl_latest") + " ▾")
            latest.setStyleSheet("color: #8b949e; font-size: 12px;")
            hdr.addWidget(lbl)
            hdr.addStretch()
            hdr.addWidget(latest)
            tpsl_lo.addLayout(hdr)

            inp_row = QHBoxLayout()
            inp_row.setSpacing(4)
            edit = QLineEdit()
            edit.setPlaceholderText(t("tpsl_order_price"))
            edit.setObjectName("trade_input")
            edit.setMinimumHeight(34)
            quote_combo = QComboBox()
            quote_combo.addItems(["USDT", "USDC"])
            _configure_combo_popup(quote_combo)
            quote_combo.setMinimumWidth(92)
            quote_combo.setMinimumHeight(34)
            inp_row.addWidget(edit, 9)
            inp_row.addWidget(quote_combo, 1)
            tpsl_lo.addLayout(inp_row)
            return edit, quote_combo

        self._tp_edit, self._tp_quote_combo = _tpsl_row("tpsl_tp")
        self._sl_edit, self._sl_quote_combo = _tpsl_row("tpsl_sl")
        lo.addWidget(self._tpsl_panel)

        self._tpsl.toggled.connect(self._tpsl_panel.setVisible)

        # ── Time in force ──
        tif_row = QHBoxLayout()
        tif_lbl = QLabel(t("time_in_force"))
        tif_lbl.setStyleSheet("color: #8b949e; font-size: 12px;")
        self._tif = QComboBox()
        self._tif.addItems(["GTC", "IOC", "FOK"])
        _configure_combo_popup(self._tif)
        self._tif.setFixedWidth(100)
        self._tif.setFixedHeight(26)
        tif_row.addWidget(tif_lbl)
        tif_row.addSpacing(4)
        tif_row.addWidget(self._tif)
        tif_row.addStretch()
        lo.addLayout(tif_row)

        # ── Long / Short action buttons ──
        action = QHBoxLayout()
        action.setSpacing(8)
        self._long_btn = QPushButton(t("go_long"))
        self._long_btn.setObjectName("long_btn")
        self._long_btn.setMinimumHeight(44)
        self._long_btn.clicked.connect(self.long_clicked)

        self._short_btn = QPushButton(t("go_short"))
        self._short_btn.setObjectName("short_btn")
        self._short_btn.setMinimumHeight(44)
        self._short_btn.clicked.connect(self.short_clicked)

        action.addWidget(self._long_btn)
        action.addWidget(self._short_btn)
        lo.addLayout(action)

        # ── Info grid (liquidation price / margin / available to open) ──
        info_lo = QVBoxLayout()
        info_lo.setContentsMargins(0, 10, 0, 6)
        info_lo.setSpacing(10)
        for label in [t("liquidation_price"), t("margin_label"), t("available_to_open")]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            l_key = QLabel(label)
            l_key.setStyleSheet("color: #8b949e; font-size: 12px;")
            l_val = QLabel("-- USDC")
            l_val.setStyleSheet("color: #e6edf3; font-size: 12px;")
            r_key = QLabel(label)
            r_key.setStyleSheet("color: #8b949e; font-size: 12px;")
            r_val = QLabel("-- USDC")
            r_val.setStyleSheet("color: #e6edf3; font-size: 12px;")
            r_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(l_key)
            row.addWidget(l_val)
            row.addStretch()
            row.addWidget(r_key)
            row.addWidget(r_val)
            info_lo.addLayout(row)
        lo.addLayout(info_lo)

    def _on_type_changed(self, idx: int) -> None:
        is_limit = (idx == 0)
        is_conditional = (idx == 2)
        self._price_edit.setEnabled(is_limit)
        self._price_edit.setPlaceholderText(
            "--" if is_limit else t("market_placeholder")
        )
        self._trigger_wrap.setVisible(is_conditional)
        if not is_conditional:
            self._trigger_edit.clear()

    def get_values(self) -> tuple:
        """Returns (order_type, qty, price, stop_price)."""
        idx = self._type_bar.current_index()
        order_type = "LIMIT" if idx == 0 else ("MARKET" if idx == 1 else "STOP_MARKET")
        price = None
        if order_type == "LIMIT":
            try:
                price = float(self._price_edit.text().strip())
            except ValueError:
                price = None
        stop_price = None
        if order_type == "STOP_MARKET":
            try:
                stop_price = float(self._trigger_edit.text().strip())
            except ValueError:
                stop_price = None
        try:
            qty = float(self._qty_edit.text().strip())
        except ValueError:
            qty = 0.0
        return order_type, qty, price, stop_price

    def set_buttons_enabled(self, enabled: bool) -> None:
        self._long_btn.setEnabled(enabled)
        self._short_btn.setEnabled(enabled)

    def set_price(self, price: str) -> None:
        """Pre-fill order price or trigger price from order book click."""
        if self._type_bar.current_index() == 2:
            self._trigger_edit.setText(price)
        elif self._type_bar.current_index() == 0:
            self._price_edit.setText(price)

    def set_quote(self, quote: str) -> None:
        """Update all quote labels/selectors when the symbol quote changes."""
        self._quote_lbl.setText(quote)
        self._trigger_quote_lbl.setText(quote)
        idx = self._tp_quote_combo.findText(quote)
        if idx >= 0:
            self._tp_quote_combo.setCurrentIndex(idx)
        idx = self._sl_quote_combo.findText(quote)
        if idx >= 0:
            self._sl_quote_combo.setCurrentIndex(idx)


def _small_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #8b949e; font-size: 12px;")
    return lbl


# ─────────────────────────── Main widget ─────────────────────────────────────

class OrderFormWidget(QWidget):
    order_placed   = pyqtSignal()

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._current_symbol = "BTCUSDT"
        self._worker = None
        self._ws_worker: _BookWsWorker | None = None
        self._platform_trades_worker: _PlatformTradesWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        # ── Left pane: order book only ──
        left_w = QWidget()
        left_lo = QVBoxLayout(left_w)
        left_lo.setContentsMargins(0, 0, 0, 0)
        left_lo.setSpacing(0)

        self._book = _OrderBookPanel()
        left_lo.addWidget(self._book, 1)

        # ── Right pane: order form + account info ──
        right_w = QWidget()
        right_lo = QVBoxLayout(right_w)
        right_lo.setContentsMargins(0, 0, 0, 0)
        right_lo.setSpacing(0)

        self._form = _OrderFormPanel(self._session)
        self._book.price_clicked.connect(self._form.set_price)
        self._form.long_clicked.connect(lambda: self._submit("BUY"))
        self._form.short_clicked.connect(lambda: self._submit("SELL"))
        right_lo.addWidget(self._form, 0)

        self._acct_panel = _AccountPanel()
        right_lo.addWidget(self._acct_panel, 1)

        # ── Horizontal splitter: order book | order form ──
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._h_splitter.setHandleWidth(1)
        self._h_splitter.setChildrenCollapsible(False)
        self._h_splitter.addWidget(left_w)
        self._h_splitter.addWidget(right_w)
        self._h_splitter.setStretchFactor(0, 2)
        self._h_splitter.setStretchFactor(1, 3)

        # ── Recent Trades (full width) ──
        self._trades_panel = _RecentTradesPanel()

        # ── Vertical splitter: (order book + form) on top, Recent Trades below ──
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(2)
        v_splitter.setChildrenCollapsible(False)
        v_splitter.addWidget(self._h_splitter)
        v_splitter.addWidget(self._trades_panel)
        v_splitter.setStretchFactor(0, 6)
        v_splitter.setStretchFactor(1, 4)
        lo.addWidget(v_splitter, 1)

        # ── Status / notice bar ──
        self._notice = QLabel("")
        self._notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._notice.setFixedHeight(24)
        self._notice.setObjectName("notice_bar")
        lo.addWidget(self._notice)

        # Debounce timer for symbol changes
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._restart_ws)
        self._restart_ws()

        # Platform trades poller (runs independently of symbol)
        self._platform_trades_worker = _PlatformTradesWorker()
        self._platform_trades_worker.refreshed.connect(self._trades_panel.refresh)
        self._platform_trades_worker.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Apply 40/60 split after the widget has a real width.
        total = self._h_splitter.width()
        if total > 0:
            self._h_splitter.setSizes([int(total * 0.45), int(total * 0.55)])

    def set_symbol(self, symbol: str) -> None:
        """Called by MainWindow when the webview URL changes to a new symbol."""
        sym = symbol.strip().upper()
        if not sym or sym == self._current_symbol:
            return
        self._current_symbol = sym
        self._debounce_timer.start(300)

    def reload_symbols(self) -> None:
        """Public method – called after a ticker sync (kept for compatibility)."""
        pass

    def _restart_ws(self) -> None:
        """Stop any running book WS worker and reconnect to the current symbol."""
        sym = self._current_symbol
        if not sym:
            return
        # ── book WS ──
        if self._ws_worker is not None:
            self._ws_worker.stop()
            try:
                self._ws_worker.ready.disconnect()
            except RuntimeError:
                pass
            self._ws_worker = None
        # ── update order book column headers and quote label for the new symbol ──
        base, quote = _parse_symbol(sym)
        self._book.set_symbol(base, quote)
        self._form.set_quote(quote)
        # ── start fresh ──
        self._ws_worker = _BookWsWorker(sym)
        self._ws_worker.ready.connect(self._book.update_book)
        self._ws_worker.start()

    def _submit(self, side: str) -> None:
        sym = self._current_symbol
        if not sym:
            self._set_notice(t("field_required", t("symbol")), "error")
            return

        order_type, qty, price, stop_price = self._form.get_values()
        if order_type == "STOP_MARKET" and (stop_price is None or stop_price <= 0):
            self._set_notice(t("field_required", t("stop_price")), "error")
            return

        if qty <= 0:
            self._set_notice(t("field_required", t("quantity")), "error")
            return
        if order_type == "LIMIT" and (price is None or price <= 0):
            self._set_notice(t("field_required", t("price")), "error")
            return

        self._form.set_buttons_enabled(False)
        self._set_notice("...", "muted")

        self._worker = _OrderWorker(
            self._session, sym, side, order_type, qty, price, stop_price
        )
        self._worker.finished.connect(self._on_done)
        self._worker.start()

    def _on_done(self, success: bool, message: str) -> None:
        self._form.set_buttons_enabled(True)
        self._set_notice(message, "success" if success else "error")
        if success:
            self.order_placed.emit()

    def _set_notice(self, msg: str, level: str = "muted") -> None:
        colors = {
            "success": "#56d364",
            "error":   "#f85149",
            "warning": "#f0883e",
            "muted":   "#8b949e",
        }
        c = colors.get(level, "#e6edf3")
        self._notice.setStyleSheet(
            f"background: #161b22; border-top: 1px solid #21262d;"
            f" color: {c}; font-size: 12px;"
        )
        self._notice.setText(msg)

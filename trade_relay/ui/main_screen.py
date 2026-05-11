"""
Main window – menu bar navigation with Trade as the default main page.
Layout of the Trade page:
  Left  (flexible): Binance Futures WebView (top) + PositionsPanel (bottom)
  Right (fixed):    OrderFormWidget (order book + order form)
"""
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QStatusBar, QMenuBar, QMessageBox,
    QSplitter,
)
from PyQt6.QtCore import pyqtSignal, QThread, Qt, QTimer, QPoint
from PyQt6.QtGui import QAction
from pathlib import Path
import json
import os
import urllib.request
import datetime

from trade_relay.i18n import t
from trade_relay.auth.manager import Session
from trade_relay import database as db
from trade_relay.ui.order_log_widget import OrderLogWidget
from trade_relay.ui.order_form_widget import OrderFormWidget
from trade_relay.ui.positions_panel import PositionsPanel
from trade_relay.ui.admin_screen import AdminWidget
from trade_relay.ui.config_screen import ConfigWidget
from trade_relay.ui.profile_screen import ProfileWidget
from trade_relay.ui.ticker_widget import TickerWidget
from trade_relay.ui.electron_bridge import ElectronBridge

# Derive Binance URL language from TRADE_RELAY_LANG env var (zh → zh-CN, others → en)
_BINANCE_LANG = "zh-CN" if os.environ.get("TRADE_RELAY_LANG", "en").lower().startswith("zh") else "en"
_DEFAULT_SYMBOL = os.environ.get("TRADE_RELAY_BINANCE_SYMBOL", "BTCUSDT").upper()
_BINANCE_FUTURES_BASE = f"https://www.binance.com/{_BINANCE_LANG}/futures/"


class _SyncTickersWorker(QThread):
    """Fetch all futures tickers from Binance and upsert into the DB."""
    finished = pyqtSignal(int, str)   # (count, error_message)

    def run(self) -> None:
        try:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read())

            rows = []
            for s in data.get("symbols", []):
                # Extract price filters
                max_price = min_price = tick_size = None
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        try:
                            max_price = float(f.get("maxPrice", 0)) or None
                            min_price = float(f.get("minPrice", 0)) or None
                            tick_size = float(f.get("tickSize", 0)) or None
                        except (ValueError, TypeError):
                            pass
                        break

                def _ts(ms):
                    if not ms:
                        return None
                    try:
                        return datetime.datetime.utcfromtimestamp(int(ms) / 1000)
                    except Exception:
                        return None

                rows.append({
                    "symbol":                s.get("symbol", ""),
                    "pair":                  s.get("pair", s.get("symbol", "")),
                    "base_asset":            s.get("baseAsset"),
                    "quote_asset":           s.get("quoteAsset"),
                    "delivery_date":         _ts(s.get("deliveryDate")),
                    "onboard_date":          _ts(s.get("onboardDate")),
                    "status":                s.get("status"),
                    "price_precision":       s.get("pricePrecision"),
                    "quantity_precision":    s.get("quantityPrecision"),
                    "base_asset_precision":  s.get("baseAssetPrecision"),
                    "quote_asset_precision": s.get("quotePrecision"),
                    "max_price":             max_price,
                    "min_price":             min_price,
                    "tick_size":             tick_size,
                })

            count = db.sync_tickers(rows)
            self.finished.emit(count, "")
        except Exception as exc:
            self.finished.emit(0, str(exc))


class MainWindow(QMainWindow):
    logout_signal = pyqtSignal()
    _app_inactive = False
    _app_state_connected = False
    _was_minimized = False
    _x11_ok = False
    _x11_minimized = False
    _restore_timer = None

    def __init__(self, session: Session) -> None:
        self._app_inactive = False
        self._app_state_connected = False
        self._was_minimized = False
        self._x11_ok = False
        self._x11_minimized = False
        self._restore_timer = None
        self._session = session
        super().__init__()
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle(t("app_title"))
        self.resize(1920, 1400)

        # ── Central stacked widget ────────────────────────────────────
        self._stack = QStackedWidget()

        # Build the main trading screen (webview + order form + positions)
        self._trading_screen = self._build_trading_screen()
        self._order_log = OrderLogWidget()
        # keep order log in sync when orders are placed
        self._order_form.order_placed.connect(self._order_log.refresh)

        self._stack.addWidget(self._trading_screen)  # index 0 – default
        self._stack.addWidget(self._order_log)        # index 1

        self._admin_idx  = None
        self._config_idx = None

        if self._session.is_admin:
            self._admin_widget = AdminWidget(self._session)
            self._admin_idx = self._stack.addWidget(self._admin_widget)

        self._config_widget = ConfigWidget(self._session)
        self._config_idx = self._stack.addWidget(self._config_widget)

        self._profile_widget = ProfileWidget(self._session)
        self._profile_idx = self._stack.addWidget(self._profile_widget)

        self._stack.setCurrentIndex(0)

        # ── Layout ─────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        layout.addWidget(self._stack)

        # ── Menu bar ───────────────────────────────────────────────────
        self._build_menubar()

        status = QStatusBar()
        status.showMessage(t("login_success", self._session.username))
        self.setStatusBar(status)

        # Start the Electron chart window after the Qt window is fully laid out.
        QTimer.singleShot(500, self._start_electron)

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        mb.setStyleSheet(
            "QMenuBar { background:#161b22; color:#e6edf3; border-bottom:1px solid #30363d; }"
            "QMenuBar::item:selected { background:#30363d; }"
            "QMenu { background:#1c2128; color:#e6edf3; border:1px solid #30363d; }"
            "QMenu::item:selected { background:#388bfd; color:white; }"
        )

        view_menu = mb.addMenu(t("view"))

        act_order = QAction(t("place_order"), self)
        act_order.triggered.connect(lambda: self._stack.setCurrentIndex(0))
        view_menu.addAction(act_order)

        act_log = QAction(t("order_log"), self)
        act_log.triggered.connect(lambda: self._stack.setCurrentIndex(1))
        view_menu.addAction(act_log)

        if self._admin_idx is not None:
            view_menu.addSeparator()
            act_admin = QAction(t("user_management"), self)
            act_admin.triggered.connect(lambda: self._stack.setCurrentIndex(self._admin_idx))
            view_menu.addAction(act_admin)

        act_profile = QAction(t("profile"), self)
        act_profile.triggered.connect(lambda: (
            self._profile_widget.refresh(),
            self._stack.setCurrentIndex(self._profile_idx),
        ))
        view_menu.addAction(act_profile)

        view_menu.addSeparator()
        act_cfg = QAction(t("settings"), self)
        act_cfg.triggered.connect(lambda: self._stack.setCurrentIndex(self._config_idx))
        view_menu.addAction(act_cfg)

        view_menu.addSeparator()
        act_sync = QAction(t("sync_tickers"), self)
        act_sync.triggered.connect(self._sync_tickers)
        view_menu.addAction(act_sync)

        view_menu.addSeparator()
        act_reload = QAction(t("reload_page"), self)
        act_reload.setShortcut("F5")
        act_reload.triggered.connect(lambda: self._bridge.reload())
        view_menu.addAction(act_reload)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setFixedHeight(50)
        header.setStyleSheet(
            "background-color: #161b22; border-bottom: 1px solid #30363d;"
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 16, 0)
        row.setSpacing(10)

        # Ticker occupies the expanding left portion of the header
        ticker = TickerWidget()
        row.addWidget(ticker, 1)

        user_lbl = QLabel(t("current_user", self._session.username))
        user_lbl.setStyleSheet("color: #8b949e;")
        row.addWidget(user_lbl)

        badge_text  = t("role_badge_admin") if self._session.is_admin else t("role_badge_user")
        badge_color = "#f0883e"             if self._session.is_admin else "#56d364"
        badge = QLabel(badge_text)
        badge.setStyleSheet(f"color: {badge_color}; font-weight: bold;")
        row.addWidget(badge)

        logout_btn = QPushButton(t("logout"))
        logout_btn.setFixedSize(80, 30)
        logout_btn.setStyleSheet("""
            QPushButton { background:#30363d; border:1px solid #30363d;
                          border-radius:4px; color:#e6edf3; }
            QPushButton:hover { background:#da3633; border-color:#da3633; }
        """)
        logout_btn.clicked.connect(self._do_logout)
        row.addWidget(logout_btn)

        return header

    def _do_logout(self) -> None:
        db.log_operation(self._session.user_id, self._session.username, "LOGOUT", "")
        self.close()
        self.logout_signal.emit()

    def _sync_tickers(self) -> None:
        self.statusBar().showMessage(t("sync_tickers_running"))
        self._sync_worker = _SyncTickersWorker()
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, count: int, error: str) -> None:
        if error:
            self.statusBar().showMessage(t("sync_tickers_fail", error))
        else:
            self.statusBar().showMessage(t("sync_tickers_ok", count))
            self._order_form.reload_symbols()

    # ── Trading screen (main layout) ───────────────────────────────────────────

    def _build_trading_screen(self) -> QWidget:
        """
        Returns the composite trading screen:
          Left (vertical splitter):
            top    – Chart placeholder (Electron BrowserWindow overlaid here)
            bottom – PositionsPanel (positions / orders / history tabs)
          Right (fixed 420 px):
            OrderFormWidget (order book + order form)
        """
        container = QWidget()
        h_lo = QHBoxLayout(container)
        h_lo.setContentsMargins(0, 0, 0, 0)
        h_lo.setSpacing(0)

        # ── Left: vertical splitter (chart / positions panel) ───────────
        v_split = QSplitter(Qt.Orientation.Vertical)
        v_split.setHandleWidth(2)
        v_split.setChildrenCollapsible(False)

        # Dark placeholder – the Electron window is positioned over this widget.
        self._chart_placeholder = QLabel("Loading Binance chart…")
        self._chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chart_placeholder.setMinimumHeight(300)
        self._chart_placeholder.setStyleSheet(
            "background:#0b0e11; color:#4a5568; font-size:14px;"
        )
        v_split.addWidget(self._chart_placeholder)

        self._positions_panel = PositionsPanel(self._session)
        self._positions_panel.setMinimumHeight(160)
        v_split.addWidget(self._positions_panel)

        v_split.setStretchFactor(0, 7)
        v_split.setStretchFactor(1, 3)

        # Forward splitter moves to Electron so the window tracks the correct size.
        self._v_split = v_split
        v_split.splitterMoved.connect(
            lambda: QTimer.singleShot(50, self._sync_electron_geometry)
        )

        # ── Right: order form ────────────────────────────────────────────
        self._order_form = OrderFormWidget(self._session)
        self._order_form.setMinimumWidth(360)
        self._order_form.order_placed.connect(self._positions_panel.refresh)

        h_lo.addWidget(v_split, 6)
        h_lo.addWidget(self._order_form, 4)

        # ── Electron bridge (started via timer in _setup_ui) ─────────────
        self._bridge = ElectronBridge(lang=_BINANCE_LANG, symbol=_DEFAULT_SYMBOL)
        self._bridge.symbol_changed.connect(self._order_form.set_symbol)
        self._bridge.load_ok.connect(self._on_electron_load_ok)
        self._bridge.error.connect(self._on_electron_error)

        return container

    # ── Electron bridge management ─────────────────────────────────────────────

    # ── X11 minimize detection ─────────────────────────────────────────────────

    def _x11_init(self) -> bool:
        """Subscribe to X11 structural/visibility events and start background event thread."""
        import threading
        try:
            from Xlib import display as Xdisplay, X
            self._xdpy = Xdisplay.Display()
            wid = int(self.winId())
            # The Qt window — we own it, so we can set its event mask
            self._x11_win = self._xdpy.create_resource_object('window', wid)
            self._x11_win.change_attributes(event_mask=(
                X.StructureNotifyMask | X.VisibilityChangeMask
            ))
            # Also subscribe on the WM frame (parent chain up to root)
            root = self._xdpy.screen().root
            w = self._x11_win
            while True:
                p = w.query_tree().parent
                if p.id == root.id:
                    break
                w = p
            self._x11_top = w
            if w.id != self._x11_win.id:
                try:
                    w.change_attributes(event_mask=(
                        X.StructureNotifyMask | X.VisibilityChangeMask
                    ))
                except Exception:
                    pass
            self._xdpy.flush()
            self._x11_minimized = False
            t = threading.Thread(target=self._x11_event_loop, daemon=True)
            t.start()
            print(f"[x11] listening: qt_wid={hex(wid)} top_wid={hex(w.id)}", flush=True)
            return True
        except Exception as exc:
            print(f"[x11] init failed: {exc}", flush=True)
            return False

    def _x11_event_loop(self) -> None:
        """Background thread: consume X11 events and update self._x11_minimized."""
        import select
        from Xlib import X
        fd = self._xdpy.fileno()
        print(f"[x11] event loop started fd={fd}", flush=True)
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 2.0)
                if not r and not self._xdpy.pending_events():
                    continue
                while self._xdpy.pending_events():
                    ev = self._xdpy.next_event()
                    if ev.type == X.UnmapNotify:
                        self._x11_minimized = True
                        print(f"[x11] UnmapNotify → minimized", flush=True)
                    elif ev.type == X.MapNotify:
                        self._x11_minimized = False
                        print(f"[x11] MapNotify → visible", flush=True)
                    elif ev.type == X.VisibilityNotify:
                        self._x11_minimized = (ev.state == X.VisibilityFullyObscured)
                        print(f"[x11] VisibilityNotify state={ev.state} → minimized={self._x11_minimized}", flush=True)
            except Exception as exc:
                print(f"[x11] event loop error: {exc}", flush=True)
                break

    # ── Electron lifecycle ─────────────────────────────────────────────────────

    def _start_electron(self) -> None:
        """Start the Electron subprocess and send initial layout."""
        self._bridge.start()
        self._sync_electron_geometry()
        if not self._app_state_connected:
            app = QApplication.instance()
            if app is not None:
                app.applicationStateChanged.connect(self._on_application_state_changed)
                self._app_state_connected = True
        self._x11_ok = self._x11_init()

        self._minimize_poll = QTimer(self)
        self._minimize_poll.setInterval(400)
        self._minimize_poll.timeout.connect(self._poll_minimize_state)
        self._minimize_poll.start()

    def _poll_minimize_state(self) -> None:
        if self._x11_ok:
            minimized = self._x11_minimized  # updated by background event thread
        else:
            minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
        active = self.isActiveWindow()
        focus_lost = self._app_inactive or not active
        minimized = minimized or focus_lost
        if minimized != self._was_minimized:
            print(f"[poll] minimized={minimized} active={active} app_inactive={self._app_inactive}", flush=True)
        self._apply_overlay_visibility(minimized)

    def _apply_overlay_visibility(self, minimized: bool) -> None:
        previous_minimized = getattr(self, "_was_minimized", False)
        if minimized == previous_minimized:
            return
        bridge = getattr(self, "_bridge", None)
        restore_timer = getattr(self, "_restore_timer", None)
        if minimized:
            if restore_timer is not None:
                restore_timer.stop()
            if bridge is not None:
                bridge.hide()
            self._was_minimized = True
            return

        if restore_timer is None:
            restore_timer = QTimer(self)
            restore_timer.setSingleShot(True)
            restore_timer.timeout.connect(self._restore_overlay_if_active)
            self._restore_timer = restore_timer
        restore_timer.start(700)

    def _restore_overlay_if_active(self) -> None:
        focus_lost = self._app_inactive or not self.isActiveWindow()
        if focus_lost:
            return
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            bridge.show()
            QTimer.singleShot(150, self._sync_electron_geometry)
        self._was_minimized = False

    def _on_application_state_changed(self, state) -> None:
        inactive = state != Qt.ApplicationState.ApplicationActive
        state_value = getattr(state, "value", state)
        print(f"[app] state={state_value} inactive={inactive}", flush=True)
        self._app_inactive = inactive
        self._apply_overlay_visibility(inactive)

    def _on_visibility_changed(self, visibility) -> None:
        from PyQt6.QtGui import QWindow
        minimized = (visibility == QWindow.Visibility.Minimized
                     or visibility == QWindow.Visibility.Hidden)
        self._apply_overlay_visibility(minimized)

    def _sync_electron_geometry(self) -> None:
        """Send the Qt main-window bounds + chart placeholder bounds to Electron.

        The overlay BrowserWindow will be repositioned to match the main window
        (so it is always perfectly aligned), and the BrowserView within it will
        be clipped to the chart placeholder area — exactly like omnitrader-ai's
        updateBinanceViewBounds() approach.
        """
        if not self._bridge.is_running():
            return

        # Main-window screen geometry
        win_geo  = self.geometry()
        win_tl   = self.mapToGlobal(QPoint(0, 0))
        # Adjust for the fact that geometry() is in frame coords on some WMs
        win_x, win_y = win_tl.x(), win_tl.y()
        win_w, win_h = self.width(), self.height()

        # Chart placeholder screen position
        chart_tl = self._chart_placeholder.mapToGlobal(QPoint(0, 0))
        chart_w  = self._chart_placeholder.width()
        chart_h  = self._chart_placeholder.height()

        self._bridge.set_layout(
            win_x, win_y, win_w, win_h,
            chart_tl.x(), chart_tl.y(), chart_w, chart_h,
        )

    def _on_electron_load_ok(self) -> None:
        self._chart_placeholder.setText("")  # blank out the loading message

    def _on_electron_error(self, message: str) -> None:
        print(f"[ElectronBridge] {message}")
        self.statusBar().showMessage(f"Binance chart: {message}", 8000)
        if not self._bridge.is_running():
            self._chart_placeholder.setText(f"⚠  {message}")

    # ── Qt event overrides ─────────────────────────────────────────────────────

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowStateChange:
            # windowState() already reflects the new state after super() call.
            state = self.windowState()
            minimized = bool(state & Qt.WindowState.WindowMinimized)
            print(f"[changeEvent] WindowStateChange minimized={minimized} state={int(state)}")
            self._apply_overlay_visibility(minimized)
        elif event.type() == QEvent.Type.ActivationChange:
            active = self.isActiveWindow()
            print(f"[changeEvent] ActivationChange active={active}", flush=True)
            self._apply_overlay_visibility(not active)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        QTimer.singleShot(0, self._sync_electron_geometry)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_electron_geometry)

    def closeEvent(self, event) -> None:
        self._bridge.stop()
        super().closeEvent(event)


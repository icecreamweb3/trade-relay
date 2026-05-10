"""
Main window – menu bar navigation with Trade as the default main page.
Layout of the Trade page:
  Left  (flexible): Binance Futures WebView (top) + PositionsPanel (bottom)
  Right (fixed):    OrderFormWidget (order book + order form)
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QStatusBar, QMenuBar, QMessageBox,
    QSplitter,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PyQt6.QtCore import pyqtSignal, QThread, QUrl, Qt
from PyQt6.QtGui import QAction
import json
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

_BINANCE_FUTURES_BASE = "https://www.binance.com/en/futures/"
_DEFAULT_SYMBOL = "BTCUSDT"


class _SilentPage(QWebEnginePage):
    """WebEnginePage that suppresses all JS console output."""
    def javaScriptConsoleMessage(self, level, message, line, source):
        pass  # swallow all JS console messages from the embedded site


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

    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
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
        act_reload.triggered.connect(lambda: self._webview.reload())
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
            top    – Binance Futures WebView
            bottom – PositionsPanel (positions / orders / history tabs)
          Right (fixed 420 px):
            OrderFormWidget (order book + order form)
        """
        container = QWidget()
        h_lo = QHBoxLayout(container)
        h_lo.setContentsMargins(0, 0, 0, 0)
        h_lo.setSpacing(0)

        # ── Left: vertical splitter (webview / positions panel) ─────────
        v_split = QSplitter(Qt.Orientation.Vertical)
        v_split.setHandleWidth(2)
        v_split.setChildrenCollapsible(False)

        self._webview = QWebEngineView()
        self._webview.setPage(_SilentPage(self._webview))
        self._inject_chart_fullscreen_script()
        self._webview.setUrl(QUrl(_BINANCE_FUTURES_BASE + _DEFAULT_SYMBOL))
        self._webview.setMinimumHeight(300)
        self._webview.loadFinished.connect(self._dismiss_webview_popups)
        self._webview.page().renderProcessTerminated.connect(self._on_render_crashed)
        self._webview.urlChanged.connect(self._on_webview_url_changed)
        v_split.addWidget(self._webview)

        self._positions_panel = PositionsPanel(self._session)
        self._positions_panel.setMinimumHeight(160)
        v_split.addWidget(self._positions_panel)

        v_split.setStretchFactor(0, 7)
        v_split.setStretchFactor(1, 3)

        # ── Right: order form ────────────────────────────────────────────
        self._order_form = OrderFormWidget(self._session)
        self._order_form.setMinimumWidth(360)
        # keep order log in sync
        # (connected in _setup_ui after _order_log is created)
        # update positions panel after placing order
        self._order_form.order_placed.connect(self._positions_panel.refresh)

        h_lo.addWidget(v_split, 6)
        h_lo.addWidget(self._order_form, 4)

        return container

    def _on_webview_url_changed(self, url) -> None:
        """Parse the symbol from the Binance futures URL and sync the order form.

        URL pattern: https://www.binance.com/en/futures/BTCUSDT
        """
        path = url.path()          # e.g. "/en/futures/BTCUSDT"
        parts = [p for p in path.split("/") if p]
        if parts and parts[-1].isalpha() and len(parts[-1]) >= 3:
            self._order_form.set_symbol(parts[-1].upper())

    def _on_render_crashed(self) -> None:
        """Reload the page when the WebEngine renderer process crashes (black screen).
        A 60-second cooldown prevents a crash-reload-crash loop.
        """
        import time as _time
        now = _time.monotonic()
        last = getattr(self, "_last_render_reload", 0.0)
        if now - last > 60:
            self._last_render_reload = now
            self._webview.reload()

    def _inject_chart_fullscreen_script(self) -> None:
        """Inject a polling script into all frames (including cross-origin TradingView
        iframe) that waits 3 s for async rendering, then polls every 1 s (max 30 times)
        for svg.chart-fullscreen-icon and simulates mousedown+mouseup to expand the chart.

        Uses sessionStorage instead of a window property so the flag survives
        TradingView iframe soft-reloads – preventing the icon being clicked a second
        time which would collapse the chart and produce a black-screen.
        """
        js = """
(function () {
    var STORAGE_KEY = '__trRelayExpanded';
    try {
        // sessionStorage persists across same-origin soft reloads within the tab.
        // If the flag is already set this iframe has already been expanded – bail out
        // to avoid toggling the chart back to collapsed state.
        if (window.sessionStorage && sessionStorage.getItem(STORAGE_KEY)) return;
    } catch (e) {}

    var attempts = 0;

    function tryExpand() {
        attempts++;
        var el = document.querySelector('svg.chart-fullscreen-icon');
        if (el) {
            try {
                if (window.sessionStorage) sessionStorage.setItem(STORAGE_KEY, '1');
            } catch (e) {}
            var rect = el.getBoundingClientRect();
            var cx = rect.left + rect.width  / 2;
            var cy = rect.top  + rect.height / 2;
            ['mousedown', 'mouseup', 'click'].forEach(function (type) {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true,
                    clientX: cx,   clientY: cy
                }));
            });
            return;
        }
        if (attempts < 30) {
            setTimeout(tryExpand, 2000);
        }
    }

    // Wait 8 s for TradingView to fully initialise and load chart data
    // before expanding – clicking too early interrupts data loading.
    setTimeout(tryExpand, 8000);
})();
"""
        script = QWebEngineScript()
        script.setName("trade_relay_chart_expand")
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)   # runs in TradingView iframe too
        self._webview.page().scripts().insert(script)

    def _dismiss_webview_popups(self) -> None:
        """Inject JS to auto-close Binance consent/cookie dialogs on page load."""
        js = r"""
(function() {
    function dismiss() {
        var phrases = [
            /I\s+Understand/i,
            /Reject\s+Additional/i,
            /Accept\s+Cookies/i,
            /Accept\s+&\s+Continue/i,
            /Got\s+it/i,
        ];
        document.querySelectorAll('button').forEach(function(btn) {
            var txt = (btn.innerText || btn.textContent || '').trim();
            for (var i = 0; i < phrases.length; i++) {
                if (phrases[i].test(txt)) { btn.click(); break; }
            }
        });
        document.querySelectorAll(
            'button[aria-label="Close"], button[aria-label="close"],' +
            '[data-bn-type="button"][class*="close"],' +
            '[class*="campaignClose"],[class*="campaign-close"],' +
            '[class*="notificationClose"],[class*="notification-close"],' +
            '[class*="popupClose"],[class*="popup-close"],' +
            '[class*="modalClose"],[class*="modal-close"],' +
            '[class*="bannerClose"],[class*="banner-close"],' +
            '[class*="challengeClose"],[class*="challenge-close"],' +
            '[class*="activityClose"],[class*="activity-close"]'
        ).forEach(function(b) { b.click(); });
    }

    dismiss();
    [800, 1800, 3000, 5000].forEach(function(ms) {
        setTimeout(dismiss, ms);
    });

    var observer = new MutationObserver(function() { dismiss(); });
    observer.observe(document.body || document.documentElement,
                     { childList: true, subtree: true });
})();
"""
        self._webview.page().runJavaScript(js)

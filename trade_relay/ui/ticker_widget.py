"""
Vertical scrolling ticker – shows recent filled orders one at a time.
Each entry is displayed for 5 s then slides upward to reveal the next.
Default message when no data: from i18n key 'ticker_default_msg'.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont, QFontMetrics
from PyQt6.QtWidgets import QWidget, QSizePolicy

from trade_relay import database as db
from trade_relay.i18n import t, current_locale

_BG          = QColor("#161b22")      # matches header background
_DEFAULT_CLR = QColor("#f0883e")      # warm amber for default
_BUY_CLR     = QColor("#3fb950")      # green  – buy / long
_SELL_CLR    = QColor("#f85149")      # red    – sell / short
_META_CLR    = QColor("#8b949e")      # grey   – labels
_PROFIT_CLR  = QColor("#3fb950")
_LOSS_CLR    = QColor("#f85149")

_DISPLAY_MS  = 5_000     # ms each item is shown before sliding
_SLIDE_STEP  = 3         # pixels per animation frame
_FPS         = 60        # animation frame rate
_REFRESH_MS  = 15_000    # re-fetch DB every 15 s
_ITEM_LIMIT  = 10        # max ticker_messages to show


def _build_items(rows: list) -> list[list[tuple[str, QColor]]]:
    """Convert ticker_messages rows into a list of display items.

    Each item is a list of (text, color) segments drawn on one line.
    Locale-aware: uses contents_zh or contents_en based on current_locale().
    """
    if not rows:
        return [[(t("ticker_default_msg"), _DEFAULT_CLR)]]

    locale = current_locale()
    items: list[list[tuple[str, QColor]]] = []
    for r in rows:
        content = r["contents_zh"] if locale == "zh" else r["contents_en"]
        content = (content or "").strip()
        if not content:
            continue
        created_at = r.get("created_at")
        time_str = ""
        if created_at:
            if hasattr(created_at, "strftime"):
                time_str = created_at.strftime("%H:%M:%S")
            else:
                time_str = str(created_at)[11:19]

        segs: list[tuple[str, QColor]] = []
        if time_str:
            segs.append((f"[{time_str}]  ", _META_CLR))
        segs.append((content, _DEFAULT_CLR))
        items.append(segs)

    return items if items else [[(t("ticker_default_msg"), _DEFAULT_CLR)]]


class TickerWidget(QWidget):
    """Vertical-scrolling ticker bar – embeds in the header row."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        self._font = QFont("Consolas, Monospace", 11)
        self._fm   = QFontMetrics(self._font)

        self._items: list[list[tuple[str, QColor]]] = [
            [(t("ticker_default_msg"), _DEFAULT_CLR)]
        ]
        self._current_idx = 0
        self._slide_y     = 0      # y offset for slide animation (px)
        self._sliding     = False

        # Display timer: trigger a slide every _DISPLAY_MS
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(_DISPLAY_MS)
        self._display_timer.timeout.connect(self._start_slide)
        self._display_timer.start()

        # Slide animation timer
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(1000 // _FPS)
        self._anim_timer.timeout.connect(self._animate_tick)

        # DB refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._fetch)
        self._refresh_timer.start()

        # Initial fetch (delayed so window is shown first)
        QTimer.singleShot(500, self._fetch)

    # ── Internal helpers ───────────────────────────────────────────────────────
    def _fetch(self) -> None:
        try:
            rows = db.get_ticker_messages(_ITEM_LIMIT)
        except Exception:
            rows = []
        new_items = _build_items(rows)
        self._items = new_items
        if self._current_idx >= len(self._items):
            self._current_idx = 0

    def _start_slide(self) -> None:
        """Begin sliding to the next item (called by _display_timer)."""
        if len(self._items) <= 1 or self._sliding:
            return
        self._sliding = True
        self._slide_y = 0
        self._anim_timer.start()

    def _animate_tick(self) -> None:
        """Advance slide animation one frame."""
        self._slide_y -= _SLIDE_STEP
        if self._slide_y <= -self.height():
            self._slide_y = 0
            self._sliding = False
            self._anim_timer.stop()
            self._current_idx = (self._current_idx + 1) % len(self._items)
        self.update()

    # ── Painting ───────────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:
        if not self._items:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setClipRect(self.rect())
        painter.fillRect(self.rect(), _BG)
        painter.setFont(self._font)

        h = self.height()
        # Baseline y for a vertically-centred single line
        line_y = (h + self._fm.ascent() - self._fm.descent()) // 2

        # Current item (may be sliding upward)
        self._draw_segments(
            painter, self._items[self._current_idx], line_y + self._slide_y
        )

        # Next item slides in from below during animation
        if self._sliding:
            next_idx = (self._current_idx + 1) % len(self._items)
            self._draw_segments(
                painter, self._items[next_idx], line_y + self._slide_y + h
            )

        painter.end()

    def _draw_segments(
        self,
        painter: QPainter,
        segments: list[tuple[str, QColor]],
        y: int,
    ) -> None:
        x = 8  # small left padding
        for text, clr in segments:
            painter.setPen(clr)
            painter.drawText(x, y, text)
            x += self._fm.horizontalAdvance(text)

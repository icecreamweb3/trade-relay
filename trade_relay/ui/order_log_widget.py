"""
Order log widget – shows all users' orders in a table with auto-refresh.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from trade_relay.i18n import t
from trade_relay import database as db

STATUS_COLORS = {
    "FILLED": "#56d364",
    "NEW":    "#56d364",
    "MOCK":   "#58a6ff",
    "FAILED": "#f85149",
    "PENDING": "#f0883e",
}
SIDE_COLORS = {
    "BUY":  "#56d364",
    "SELL": "#f85149",
}

COLUMNS = [
    ("#",            40),
    ("col_time",    155),
    ("col_user",     90),
    ("col_symbol",   90),
    ("col_side",     60),
    ("col_type",     80),
    ("col_qty",      90),
    ("col_price",    100),
    ("col_status",   80),
    ("col_order_id", -1),   # -1 = stretch
]


class OrderLogWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()
        # Auto-refresh every 10 s
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(10_000)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        refresh_btn = QPushButton(t("refresh"))
        refresh_btn.setFixedWidth(90)
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(
            ["#" if k == "#" else t(k) for k, _ in COLUMNS]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(True)
        self._table.setSortingEnabled(False)

        hdr = self._table.horizontalHeader()
        for i, (_, width) in enumerate(COLUMNS):
            if width == -1:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                self._table.setColumnWidth(i, width)

        layout.addWidget(self._table)
        self.refresh()

    def refresh(self) -> None:
        orders = db.get_all_orders()
        self._table.setRowCount(len(orders))
        for row, order in enumerate(orders):
            price_str = f"{order['price']:.4f}" if order["price"] else "-"
            values = [
                str(order["id"]),
                str(order["created_at"])[:19],
                order["username"],
                order["symbol"],
                order["side"],
                order["order_type"],
                str(order["quantity"]),
                price_str,
                order["status"],
                order["binance_order_id"] or "-",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
                )
                if col == 4:  # side
                    item.setForeground(QColor(SIDE_COLORS.get(val, "#e6edf3")))
                elif col == 8:  # status
                    item.setForeground(QColor(STATUS_COLORS.get(val, "#e6edf3")))
                elif col == 2:  # username
                    item.setForeground(QColor("#8b949e"))
                self._table.setItem(row, col, item)
        self._table.scrollToTop()

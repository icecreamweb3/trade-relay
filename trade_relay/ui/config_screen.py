"""
Settings widget – per-user Binance API key configuration.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QGroupBox,
)
from PyQt6.QtCore import Qt

from trade_relay.i18n import t
from trade_relay.auth.manager import Session
from trade_relay import config as cfg


class ConfigWidget(QWidget):
    def __init__(self, session: Session) -> None:
        super().__init__()
        self._session = session
        self._setup_ui()
        self._load()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        outer.setContentsMargins(20, 20, 20, 20)

        box = QGroupBox(t("settings"))
        box.setFixedWidth(520)
        form = QFormLayout(box)
        form.setSpacing(12)
        form.setContentsMargins(24, 20, 24, 20)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText(t("api_key"))
        form.addRow(t("api_key"), self._api_key)

        self._api_secret = QLineEdit()
        self._api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_secret.setPlaceholderText(t("api_secret"))
        form.addRow(t("api_secret"), self._api_secret)

        self._testnet = QCheckBox()
        form.addRow(t("testnet"), self._testnet)

        self._mock_mode = QCheckBox()
        form.addRow(t("mock_mode"), self._mock_mode)

        self._notice = QLabel("")
        self._notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._notice.setMinimumHeight(20)
        form.addRow("", self._notice)

        save_btn = QPushButton(t("save_settings"))
        save_btn.setObjectName("primary")
        save_btn.setMinimumHeight(36)
        save_btn.clicked.connect(self._save)
        form.addRow("", save_btn)

        outer.addWidget(box)

    def _load(self) -> None:
        user_cfg = cfg.load_user_config(self._session.username)
        binance = user_cfg.get("binance", {})
        trading = user_cfg.get("trading", {})
        self._api_key.setText(binance.get("api_key", ""))
        self._api_secret.setText(binance.get("api_secret", ""))
        self._testnet.setChecked(bool(binance.get("testnet", False)))
        self._mock_mode.setChecked(bool(trading.get("mock_mode", False)))

    def _save(self) -> None:
        try:
            cfg.save_user_config(self._session.username, {
                "binance": {
                    "api_key":    self._api_key.text().strip(),
                    "api_secret": self._api_secret.text().strip(),
                    "testnet":    self._testnet.isChecked(),
                },
                "trading": {
                    "mock_mode": self._mock_mode.isChecked(),
                },
            })
            self._set_notice(t("settings_saved"), "success")
        except Exception as exc:
            self._set_notice(t("settings_error", str(exc)), "error")

    def _set_notice(self, msg: str, level: str = "muted") -> None:
        colors = {"success": "#56d364", "error": "#f85149", "muted": "#8b949e"}
        self._notice.setStyleSheet(f"color: {colors.get(level, '#e6edf3')};")
        self._notice.setText(msg)

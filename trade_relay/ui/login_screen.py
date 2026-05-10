"""
Login dialog – shown on startup and after logout.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
)
from PyQt6.QtCore import Qt

from trade_relay.i18n import t
from trade_relay.auth.manager import login, Session


class LoginDialog(QDialog):
    """Modal login window. On success, self.session is set and dialog is accepted."""

    def __init__(self) -> None:
        super().__init__()
        self.session: Session | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle(t("app_title"))
        self.setFixedSize(440, 380)
        self.setWindowFlags(Qt.WindowType.Dialog)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 36, 48, 36)
        layout.setSpacing(10)

        title = QLabel(t("app_title"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 17px; font-weight: bold; color: #58a6ff;")
        layout.addWidget(title)

        subtitle = QLabel(t("app_subtitle"))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(subtitle)

        layout.addSpacing(12)

        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error.setStyleSheet("color: #f85149; min-height: 18px;")
        layout.addWidget(self._error)

        user_lbl = QLabel(t("username"))
        user_lbl.setStyleSheet("color: #8b949e;")
        layout.addWidget(user_lbl)

        self._username = QLineEdit()
        self._username.setPlaceholderText(t("username"))
        self._username.setText("admin")
        self._username.returnPressed.connect(self._do_login)
        layout.addWidget(self._username)

        pwd_lbl = QLabel(t("password"))
        pwd_lbl.setStyleSheet("color: #8b949e;")
        layout.addWidget(pwd_lbl)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText(t("password"))
        self._password.setText("Admin@123")
        self._password.returnPressed.connect(self._do_login)
        layout.addWidget(self._password)

        layout.addSpacing(10)

        btn = QPushButton(t("login_btn"))
        btn.setObjectName("primary")
        btn.setMinimumHeight(38)
        btn.clicked.connect(self._do_login)
        layout.addWidget(btn)

        self._username.setFocus()

    def _do_login(self) -> None:
        username = self._username.text().strip()
        password = self._password.text()

        if not username or not password:
            self._error.setText(t("login_failed"))
            return

        session = login(username, password)
        if session is None:
            self._error.setText(t("login_failed"))
            self._password.clear()
            self._password.setFocus()
            return

        self.session = session
        self.accept()

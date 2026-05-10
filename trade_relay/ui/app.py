"""
PyQt6 application entry point.
Handles login → main window → logout → re-login lifecycle.
"""
import os
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from trade_relay.ui.styles import build_stylesheet
from trade_relay.ui.login_screen import LoginDialog
from trade_relay.ui.main_screen import MainWindow


def run_app() -> None:
    # Disable GPU/Vulkan for QtWebEngine on Linux to avoid dma_buf / Vulkan errors.
    # Must be set before QApplication is created.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                          "--disable-gpu --disable-software-rasterizer")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Trade Relay")
    app.setStyleSheet(build_stylesheet())

    _show_login(app)
    sys.exit(app.exec())


def _show_login(app: QApplication) -> None:
    dialog = LoginDialog()
    if dialog.exec() != LoginDialog.DialogCode.Accepted or dialog.session is None:
        app.quit()
        return

    window = MainWindow(dialog.session)
    # Keep a reference on app to prevent Python GC from destroying the window
    app._main_window = window  # type: ignore[attr-defined]
    # Re-show login when user logs out
    window.logout_signal.connect(lambda: _show_login(app))
    window.show()

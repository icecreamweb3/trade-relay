"""
PyQt6 application entry point.
Handles login → main window → logout → re-login lifecycle.
"""
import os
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer

from trade_relay.ui.styles import build_stylesheet
from trade_relay.ui.login_screen import LoginDialog
from trade_relay.ui.main_screen import MainWindow


def _patch_main_window_runtime() -> None:
    MainWindow._app_inactive = False
    MainWindow._app_state_connected = False
    MainWindow._was_minimized = False
    MainWindow._x11_ok = False
    MainWindow._x11_minimized = False

    def _safe_apply_overlay_visibility(self, minimized: bool) -> None:
        previous_minimized = getattr(self, "_was_minimized", False)
        if minimized == previous_minimized:
            return
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            if minimized:
                bridge.hide()
            else:
                bridge.show()
                QTimer.singleShot(150, self._sync_electron_geometry)
        self._was_minimized = minimized

    MainWindow._apply_overlay_visibility = _safe_apply_overlay_visibility


_patch_main_window_runtime()


def run_app() -> None:
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

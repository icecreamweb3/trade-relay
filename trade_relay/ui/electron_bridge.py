"""
ElectronBridge – manages the Electron subprocess that renders the Binance
Futures chart and communicates with it over a local TCP socket (NDJSON).

Architecture
────────────
  Python (PyQt6)                    Electron subprocess
  ─────────────────────             ───────────────────
  ElectronBridge  ←──TCP NDJSON──→  main.js TCP server
       │
       └─ _TcpClientThread  (QThread, reads incoming messages)

Commands Python → Electron  (written by ElectronBridge.send):
  { "type": "geometry", "x": …, "y": …, "width": …, "height": … }
  { "type": "navigate", "url": … }
  { "type": "reload" }
  { "type": "show" } / { "type": "hide" }

Events Electron → Python  (received by _TcpClientThread, emitted as Qt signals):
  { "type": "symbol_change", "symbol": … }
  { "type": "load_ok" }
  { "type": "render_crash", "reason": … }
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# Directory containing electron/main.js  (two levels up from this file)
_ELECTRON_DIR = Path(__file__).parent.parent.parent / "electron"
_DEFAULT_PORT = 19877


class _TcpClientThread(QThread):
    """Background thread: connects to Electron's TCP server and reads messages.

    Buffers incoming bytes, splits on newlines, parses JSON and emits
    ``message_received``.  Uses a simple retry loop so the thread can be
    started before Electron's ``server.listen`` finishes.
    """

    message_received = pyqtSignal(dict)
    connected        = pyqtSignal()
    disconnected     = pyqtSignal()

    def __init__(self, port: int) -> None:
        super().__init__()
        self._port        = port
        self._sock: socket.socket | None = None
        self._running     = False
        self._pending: list[str] = []   # messages queued before connect

    # ── Public API (called from main thread) ──────────────────────────────────

    def enqueue(self, msg: dict) -> None:
        """Queue a message for sending.  Safe to call before connect."""
        line = json.dumps(msg) + "\n"
        if self._sock and not self._sock.fileno() == -1:
            try:
                self._sock.sendall(line.encode())
                return
            except OSError:
                pass
        self._pending.append(line)

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    # ── QThread.run ───────────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True

        # Connect with exponential back-off (up to ~9 s total)
        for attempt in range(30):
            if not self._running:
                return
            try:
                self._sock = socket.create_connection(
                    ("127.0.0.1", self._port), timeout=2.0
                )
                break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3 * (1.0 + attempt * 0.05))
        else:
            self.disconnected.emit()
            return

        # Flush messages that were queued while we were connecting
        for line in self._pending:
            try:
                self._sock.sendall(line.encode())
            except OSError:
                pass
        self._pending.clear()

        self.connected.emit()

        # Read loop
        self._sock.settimeout(1.0)
        buf = ""
        while self._running:
            try:
                chunk = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not chunk:
                    break
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            self.message_received.emit(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            except socket.timeout:
                continue
            except OSError:
                break

        self.disconnected.emit()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None


class ElectronBridge(QObject):
    """Manages the Electron subprocess and NDJSON TCP IPC.

    Signals
    ───────
    symbol_changed(str)  – emitted when the user navigates to a new pair
    load_ok()            – emitted when Binance page finishes loading
    error(str)           – emitted for subprocess / IPC errors
    """

    symbol_changed = pyqtSignal(str)
    load_ok        = pyqtSignal()
    error          = pyqtSignal(str)

    def __init__(
        self,
        lang:   str = "en",
        symbol: str = "BTCUSDT",
        port:   int = _DEFAULT_PORT,
    ) -> None:
        super().__init__()
        self._lang    = lang
        self._symbol  = symbol
        self._port    = port
        self._proc:   subprocess.Popen | None    = None
        self._thread: _TcpClientThread | None    = None
        self._running = False
        self._last_geometry: dict | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Find Electron, optionally install deps, and spawn the subprocess."""
        electron_bin = self._find_electron()
        if electron_bin is None:
            self.error.emit(
                "Electron not found. "
                "Run:  cd electron && npm install  (requires Node.js)"
            )
            return

        import signal as _signal, ctypes as _ct

        def _set_pdeathsig():
            """Ask kernel to send SIGTERM to Electron when the Python process dies."""
            try:
                _ct.CDLL("libc.so.6").prctl(1, _signal.SIGTERM)  # PR_SET_PDEATHSIG=1
            except Exception:
                pass

        env = {**os.environ, "ELECTRON_ENABLE_LOGGING": "false"}
        try:
            self._proc = subprocess.Popen(
                [
                    electron_bin,
                    str(_ELECTRON_DIR / "main.js"),
                    f"--port={self._port}",
                    f"--lang={self._lang}",
                    f"--symbol={self._symbol}",
                    "--no-sandbox",
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=_set_pdeathsig,
            )
        except FileNotFoundError as exc:
            self.error.emit(f"Failed to launch Electron: {exc}")
            return

        import atexit
        atexit.register(self.stop)

        self._running = True
        self._thread  = _TcpClientThread(self._port)
        self._thread.message_received.connect(self._on_message)
        self._thread.connected.connect(self._on_connected)
        self._thread.disconnected.connect(self._on_disconnected)
        self._thread.start()

    def stop(self) -> None:
        """Terminate the Electron subprocess and stop the IPC thread."""
        self._running = False
        if self._thread:
            self._thread.stop()
            self._thread.wait(3000)
            self._thread = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def is_running(self) -> bool:
        return self._running and self._proc is not None and self._proc.poll() is None

    # ── Commands ──────────────────────────────────────────────────────────────

    def set_geometry(self, x: int, y: int, width: int, height: int) -> None:
        """Legacy: send chart-only geometry (used when win bounds not yet known)."""
        msg = {"type": "geometry", "x": x, "y": y, "width": width, "height": height}
        self._last_geometry = msg
        self._send(msg)

    def set_layout(
        self,
        win_x: int, win_y: int, win_w: int, win_h: int,
        chart_x: int, chart_y: int, chart_w: int, chart_h: int,
    ) -> None:
        """Preferred: update overlay window bounds + BrowserView bounds in one call.
        All coordinates are in screen-space pixels.
        """
        msg = {
            "type": "layout",
            "win":   {"x": win_x, "y": win_y, "w": win_w, "h": win_h},
            "chart": {"x": chart_x, "y": chart_y, "w": chart_w, "h": chart_h},
        }
        self._last_geometry = msg
        self._send(msg)

    def navigate(self, url: str) -> None:
        self._send({"type": "navigate", "url": url})

    def reload(self) -> None:
        self._send({"type": "reload"})

    def show(self) -> None:
        self._send({"type": "show"})

    def hide(self) -> None:
        self._send({"type": "hide"})

    def focus(self) -> None:
        """No-op in BrowserView architecture; kept for API compatibility."""

    def set_parent_wid(self, wid: int) -> None:
        """No-op in BrowserView architecture; kept for API compatibility."""

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send(self, msg: dict) -> None:
        if self._thread:
            self._thread.enqueue(msg)

    def _on_connected(self) -> None:
        # Re-send geometry so Electron positions and shows the window.
        if self._last_geometry:
            self._send(self._last_geometry)

    def _on_disconnected(self) -> None:
        if self._running:
            self.error.emit("Electron IPC connection closed unexpectedly")

    def _on_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "symbol_change":
            sym = msg.get("symbol", "").strip().upper()
            if sym:
                self.symbol_changed.emit(sym)
        elif t == "load_ok":
            self.load_ok.emit()
        elif t == "render_crash":
            self.error.emit(f"Binance renderer crashed: {msg.get('reason', 'unknown')}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _find_electron() -> str | None:
        """Return path to the electron binary, or None if not found."""
        # 1. Locally installed in trade-relay/electron/node_modules
        local = _ELECTRON_DIR / "node_modules" / ".bin" / "electron"
        if local.exists():
            return str(local)

        # 2. Globally installed
        g = shutil.which("electron")
        if g:
            return g

        return None

    @staticmethod
    def install_deps() -> bool:
        """Run ``npm install`` in the electron directory (blocking).

        Returns True on success.  Called by the setup script.
        """
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(_ELECTRON_DIR),
            capture_output=False,
        )
        return result.returncode == 0

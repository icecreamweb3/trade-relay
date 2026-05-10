#!/usr/bin/env python3
"""
Trade Relay – entry point.

Default admin account (created on first run):
  Username : admin  (set TRADE_RELAY_ADMIN_USERNAME in .env)
  Password : Admin@123 (set TRADE_RELAY_ADMIN_PASSWORD in .env)

Language override (highest → lowest priority):
  python main.py --lang zh       # CLI flag
  TRADE_RELAY_LANG=zh in .env    # .env file
  Platform default (Windows→zh, Linux→en)

Copy .env.example → .env and edit to configure the application.
"""
import sys
import os
import argparse
from pathlib import Path

# Add project root to sys.path (needed when running as packaged executable)
_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

# ── Load .env before anything else ──────────────────────────────────────────
# We do this with a minimal inline loader so the app works even if
# python-dotenv is not yet installed.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_ROOT / ".env", override=False)
except ImportError:
    # Fallback: parse .env manually (key=value, ignore comments/blanks)
    _env_path = _ROOT / ".env"
    if _env_path.exists():
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

# On Linux, prefer X11 (xcb) over Wayland to avoid missing wl_display errors.
# QT_QPA_PLATFORM can also be set in .env to override this default.
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# ── Parse --lang CLI flag (takes priority over .env) ────────────────────────
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--lang", choices=["zh", "en"], default=None,
                     help="Override display language: zh (Chinese) or en (English)")
_args, _remaining = _parser.parse_known_args()
sys.argv = [sys.argv[0]] + _remaining  # pass remaining args to Qt

from trade_relay.i18n import set_locale  # noqa: E402
if _args.lang:
    set_locale(_args.lang)

from trade_relay.database import init_db
from trade_relay.auth.manager import ensure_admin_exists
from trade_relay.ui.app import run_app


def main() -> None:
    init_db()
    ensure_admin_exists()
    run_app()


if __name__ == "__main__":
    main()

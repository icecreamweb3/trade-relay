"""Shared websocket proxy helpers for Binance streams."""

from __future__ import annotations

import logging
import os
import platform

from trade_relay.env_loader import load_env

logger = logging.getLogger(__name__)


def get_default_proxy_port() -> int:
    system = platform.system().lower()
    if system == "darwin":
        return 1087
    if system == "linux":
        return 10809
    return 10809


def get_proxy_config() -> tuple[bool, str | None, int | None]:
    load_env()
    proxy = os.getenv("PROXY", "").strip()
    if not proxy:
        return False, None, None

    try:
        if "://" in proxy:
            proxy = proxy.split("://", 1)[1]

        if ":" in proxy:
            host, port_str = proxy.rsplit(":", 1)
            return True, host, int(port_str)
        return True, proxy, get_default_proxy_port()
    except Exception as exc:
        logger.warning("Failed to parse PROXY=%r: %s", proxy, exc)
        return False, None, None
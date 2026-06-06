"""
Configuration management: per-user Binance API credentials stored in database.
"""
import os
from pathlib import Path
from typing import Optional

from trade_relay import database as db_module

# ---------------------------------------------------------------------------
# Legacy YAML helpers — kept only for one-time migration tooling.
# New code must NOT call load_user_config / save_user_config.
# ---------------------------------------------------------------------------
try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore

CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "users"


def _config_path(username: str) -> Path:
    safe = "".join(c for c in username if c.isalnum() or c in ("_", "-"))
    return CONFIGS_DIR / f"{safe}.yaml"


def _load_yaml_config(username: str) -> dict:
    """Read legacy YAML config. Returns empty dict when not available."""
    path = _config_path(username)
    if not path.exists() or _yaml is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API — DB is the single source of truth.
# ---------------------------------------------------------------------------

def get_api_key(username: str) -> Optional[str]:
    """Return Binance API key from DB, falling back to env var."""
    row = db_module.get_user_by_username(username)
    if row is not None:
        db_value = db_module.decrypt_api_credential(row.get("binance_api_key") or "")
        if db_value:
            return db_value
    return os.environ.get("TRADE_RELAY_BINANCE_API_KEY", "").strip() or None


def get_api_secret(username: str) -> Optional[str]:
    """Return Binance API secret from DB, falling back to env var."""
    row = db_module.get_user_by_username(username)
    if row is not None:
        db_value = db_module.decrypt_api_credential(row.get("binance_api_secret") or "")
        if db_value:
            return db_value
    return os.environ.get("TRADE_RELAY_BINANCE_API_SECRET", "").strip() or None


def is_testnet(username: str) -> bool:
    """Return testnet flag from DB, falling back to env var."""
    row = db_module.get_user_by_username(username)
    if row is not None and row.get("testnet") is not None:
        return bool(row["testnet"])
    return os.environ.get("TRADE_RELAY_BINANCE_TESTNET", "false").strip().lower() in ("1", "true", "yes")


def is_mock_mode(username: str) -> bool:
    """Return mock_mode flag from DB, falling back to env var."""
    row = db_module.get_user_by_username(username)
    if row is not None and row.get("mock_mode") is not None:
        return bool(row["mock_mode"])
    return os.environ.get("TRADE_RELAY_MOCK_MODE", "false").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Deprecated stubs — do not call from new code.
# ---------------------------------------------------------------------------

def load_user_config(username: str) -> dict:
    """Deprecated: reads legacy YAML file only. Use DB functions instead."""
    return _load_yaml_config(username)


def save_user_config(username: str, config: dict) -> None:
    """Deprecated: no-op. Config is now stored in the database."""
    pass


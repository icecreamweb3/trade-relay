"""
Configuration management: per-user YAML config files for Binance keys.
"""
import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

CONFIGS_DIR = Path(__file__).parent.parent / "configs" / "users"


def _ensure_dir() -> None:
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)


def _config_path(username: str) -> Path:
    # Sanitize username to prevent path traversal
    safe = "".join(c for c in username if c.isalnum() or c in ("_", "-"))
    return CONFIGS_DIR / f"{safe}.yaml"


def load_user_config(username: str) -> dict:
    """Load user Binance config. Returns defaults if not found."""
    _ensure_dir()
    path = _config_path(username)
    defaults = {
        "binance": {
            "api_key": "",
            "api_secret": "",
            "testnet": False,
        },
        "trading": {
            "mock_mode": False,
        },
    }
    if not path.exists():
        return defaults
    try:
        if yaml is None:
            raise RuntimeError("pyyaml not installed")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Merge with defaults
        for section, values in defaults.items():
            if section not in data:
                data[section] = values
            else:
                for k, v in values.items():
                    if k not in data[section]:
                        data[section][k] = v
        return data
    except Exception:
        return defaults


def save_user_config(username: str, config: dict) -> None:
    """Save user config to YAML file."""
    _ensure_dir()
    path = _config_path(username)
    if yaml is None:
        raise RuntimeError("pyyaml not installed")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def get_api_key(username: str) -> Optional[str]:
    """Return API key: per-user YAML → env TRADE_RELAY_BINANCE_API_KEY."""
    cfg = load_user_config(username)
    return (cfg.get("binance", {}).get("api_key")
            or os.environ.get("TRADE_RELAY_BINANCE_API_KEY", "").strip()
            or None)


def get_api_secret(username: str) -> Optional[str]:
    """Return API secret: per-user YAML → env TRADE_RELAY_BINANCE_API_SECRET."""
    cfg = load_user_config(username)
    return (cfg.get("binance", {}).get("api_secret")
            or os.environ.get("TRADE_RELAY_BINANCE_API_SECRET", "").strip()
            or None)


def is_testnet(username: str) -> bool:
    """Testnet flag: per-user YAML → env TRADE_RELAY_BINANCE_TESTNET."""
    cfg = load_user_config(username)
    yaml_val = cfg.get("binance", {}).get("testnet")
    if yaml_val:
        return True
    return os.environ.get("TRADE_RELAY_BINANCE_TESTNET", "false").strip().lower() in ("1", "true", "yes")


def is_mock_mode(username: str) -> bool:
    """Mock mode flag: per-user YAML → env TRADE_RELAY_MOCK_MODE."""
    cfg = load_user_config(username)
    yaml_val = cfg.get("trading", {}).get("mock_mode")
    if yaml_val:
        return True
    return os.environ.get("TRADE_RELAY_MOCK_MODE", "false").strip().lower() in ("1", "true", "yes")

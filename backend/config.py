"""
Backend configuration — reads from .env.production first, then falls back to .env.
"""
import os
from trade_relay.env_loader import load_env

load_env(override=False)


def _env(*names: str, default: str) -> str:
	for name in names:
		value = os.getenv(name)
		if value not in (None, ""):
			return value
	return default

# MySQL
DB_HOST     = _env("TRADE_RELAY_MYSQL_HOST", "DB_HOST", default="127.0.0.1")
DB_PORT     = int(_env("TRADE_RELAY_MYSQL_PORT", "DB_PORT", default="3306"))
DB_USER     = _env("TRADE_RELAY_MYSQL_USER", "DB_USER", default="root")
DB_PASSWORD = _env("TRADE_RELAY_MYSQL_PASSWORD", "DB_PASSWORD", default="")
DB_NAME     = _env("TRADE_RELAY_MYSQL_DATABASE", "DB_NAME", default="trade_relay")

# JWT
JWT_SECRET  = os.getenv("TRADE_RELAY_JWT_SECRET", "change-me-in-production-secret")
JWT_ALGO    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))

# Encryption (for Binance API keys in DB)
ENCRYPTION_KEY = os.getenv("TRADE_RELAY_ENCRYPTION_KEY", "")

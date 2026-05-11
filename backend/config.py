"""
Backend configuration — reads from .env file.
"""
import os
from dotenv import load_dotenv
from pathlib import Path

# Load from project root .env
load_dotenv(Path(__file__).parent.parent / ".env")

# MySQL
DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "trade_relay")

# JWT
JWT_SECRET  = os.getenv("TRADE_RELAY_JWT_SECRET", "change-me-in-production-secret")
JWT_ALGO    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))

# Encryption (for Binance API keys in DB)
ENCRYPTION_KEY = os.getenv("TRADE_RELAY_ENCRYPTION_KEY", "")

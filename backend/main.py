"""
Trade Relay — FastAPI Backend
Provides REST API for the Electron + React frontend.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import account, auth, users, orders, positions, config, profile
from .logger import setup_logging, get_logger

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    _log.info("Trade Relay backend starting up")
    # Initialize DB schema and ensure default admin exists
    from trade_relay import database as db_module
    from trade_relay.auth.manager import ensure_admin_exists
    from trade_relay.trading.order_status_stream import (
        restore_active_order_status_streams,
        stop_all_order_status_streams,
        sync_active_orders_on_startup,
    )
    db_module.init_db()
    ensure_admin_exists()
    restore_active_order_status_streams()
    sync_active_orders_on_startup()
    _log.info("Database initialised")
    yield
    stop_all_order_status_streams()
    _log.info("Trade Relay backend shutting down")


app = FastAPI(title="Trade Relay Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "app://."],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(orders.router)
app.include_router(positions.router)
app.include_router(config.router)
app.include_router(profile.router)
app.include_router(account.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Trade Relay Backend"}

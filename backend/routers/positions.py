"""
Positions router: current positions, open orders, order history, trade history.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from trade_relay import database as db_module
from trade_relay import config as cfg_module
from trade_relay.exchange.binance_client import BinanceClient
from backend.routers.auth import get_current_user
from backend.logger import get_logger

router = APIRouter(prefix="/api/positions", tags=["positions"])
_log = get_logger(__name__)


class PositionOut(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: float
    entry_price: Optional[float]
    liquidation_price: Optional[float]
    unrealized_pnl: Optional[float]
    leverage: int
    margin_type: str
    margin: Optional[float]


def _margin_from_exchange_row(row: dict) -> float | None:
    isolated_wallet = float(row.get("isolatedWallet", 0) or 0)
    if isolated_wallet > 0:
        return isolated_wallet

    for key in ("positionInitialMargin", "initialMargin"):
        value = float(row.get(key, 0) or 0)
        if value > 0:
            return value

    leverage = float(row.get("leverage", 0) or 0)
    notional = abs(float(row.get("notional", 0) or 0))
    if leverage > 0 and notional > 0:
        return notional / leverage
    return None


def _position_side_from_exchange_row(row: dict, position_amt: float) -> str:
    side = str(row.get("positionSide", "") or "").upper()
    if side in ("LONG", "SHORT"):
        return side
    return "LONG" if position_amt > 0 else "SHORT"


def _live_positions(username: str) -> list[PositionOut]:
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if not api_key or not api_secret:
        return []

    client = BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=cfg_module.is_testnet(username),
    )
    rows = client.get_positions()
    positions: list[PositionOut] = []
    for index, row in enumerate(rows, start=1):
        position_amt = float(row.get("positionAmt", 0) or 0)
        if position_amt == 0:
            continue
        positions.append(
            PositionOut(
                id=index,
                symbol=str(row.get("symbol", "") or ""),
                side=_position_side_from_exchange_row(row, position_amt),
                quantity=abs(position_amt),
                entry_price=float(row.get("entryPrice", 0) or 0),
                liquidation_price=float(row.get("liquidationPrice", 0) or 0),
                unrealized_pnl=float(
                    row.get("unrealizedProfit", row.get("unRealizedProfit", 0)) or 0
                ),
                leverage=int(float(row.get("leverage", 0) or 0)),
                margin_type=str(row.get("marginType", "") or "").upper(),
                margin=_margin_from_exchange_row(row),
            )
        )
    return positions


def _db_positions(user_id: int | None) -> list[PositionOut]:
    rows = db_module.get_positions(user_id=user_id)
    positions: list[PositionOut] = []
    for index, row in enumerate(rows, start=1):
        positions.append(
            PositionOut(
                id=int(row.get("id") or index),
                symbol=r["symbol"],
                side=str(row.get("position_side", "") or "").upper(),
                quantity=float(row["quantity"]),
                entry_price=float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None,
                liquidation_price=None,
                unrealized_pnl=float(row["unrealized_pnl"]) if row.get("unrealized_pnl") is not None else None,
                leverage=int(row.get("leverage") or 0),
                margin_type=str(row.get("margin_type", "") or "").upper(),
                margin=None,
            )
        )
    return positions


@router.get("", response_model=list[PositionOut])
def get_positions(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    try:
        live_rows = _live_positions(user["username"])
        if live_rows:
            return live_rows
    except Exception:
        _log.exception("Live position query failed for user=%s", user["username"])

    return _db_positions(user_id=user_id)

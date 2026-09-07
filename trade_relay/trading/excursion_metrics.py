"""持仓周期 MFE / MAE 与退出质量指标。

最终曲线采用“已实现盈亏 + 剩余仓位按价格估值 - 累计手续费”的净值口径。
K 线只有分钟级 OHLC，因此同一分钟内的先后顺序不可知；成交价会作为额外采样点，
结果用于复盘统计，不应用作撮合级审计数据。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


EPSILON = 1e-10
ALGORITHM_VERSION = 1


class ExcursionCalculationError(ValueError):
    pass


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _timestamp(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ExcursionCalculationError("成交记录缺少有效时间")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def order_time(order: dict) -> datetime:
    return _timestamp(order.get("filled_at") or order.get("updated_at") or order.get("created_at"))


def order_quantity(order: dict) -> float:
    return abs(_number(order.get("filled_qty") or order.get("quantity")))


def order_price(order: dict) -> float:
    return _number(order.get("avg_price") or order.get("price") or order.get("stop_price"))


def order_position_side(order: dict) -> Optional[str]:
    direction = str(order.get("trade_direction") or "").upper()
    side = str(order.get("side") or "").upper()
    if direction == "OPEN":
        return "LONG" if side == "BUY" else "SHORT" if side == "SELL" else None
    if direction == "CLOSE":
        return "LONG" if side == "SELL" else "SHORT" if side == "BUY" else None
    return None


def split_complete_cycles(orders: Iterable[dict], position_side: str) -> list[list[dict]]:
    """按成交数量把订单重建为从空仓到再次空仓的完整周期。"""
    wanted_side = position_side.upper()
    relevant = [
        row for row in orders
        if order_position_side(row) == wanted_side and order_quantity(row) > EPSILON
    ]
    relevant.sort(key=lambda row: (order_time(row), int(row.get("id") or 0)))

    cycles: list[list[dict]] = []
    current: list[dict] = []
    quantity = 0.0
    for row in relevant:
        direction = str(row.get("trade_direction") or "").upper()
        qty = order_quantity(row)
        if direction == "OPEN":
            if quantity <= EPSILON:
                current = []
            current.append(row)
            quantity += qty
            continue
        if direction != "CLOSE" or quantity <= EPSILON:
            continue
        current.append(row)
        quantity = max(0.0, quantity - min(quantity, qty))
        if quantity <= EPSILON:
            cycles.append(current)
            current = []
            quantity = 0.0
    return cycles


def choose_position_cycle(
    orders: Iterable[dict],
    position_side: str,
    target_close_order_ids: Iterable[int] = (),
    closed_at: Optional[datetime] = None,
) -> list[dict]:
    cycles = split_complete_cycles(orders, position_side)
    if not cycles:
        raise ExcursionCalculationError("没有找到从开仓到完全平仓的完整成交周期")

    targets = {int(value) for value in target_close_order_ids if value is not None}
    if targets:
        for cycle in reversed(cycles):
            ids = {int(row.get("id") or 0) for row in cycle}
            if ids & targets:
                return cycle

    if closed_at is None:
        return cycles[-1]
    normalized_close = _timestamp(closed_at)
    return min(cycles, key=lambda cycle: abs((order_time(cycle[-1]) - normalized_close).total_seconds()))


@dataclass(frozen=True)
class PriceSample:
    at: datetime
    low: float
    high: float


def parse_kline_samples(raw_klines: Iterable[list]) -> list[PriceSample]:
    samples: list[PriceSample] = []
    for row in raw_klines:
        if len(row) < 7:
            continue
        samples.append(
            PriceSample(
                at=datetime.utcfromtimestamp(int(row[6]) / 1000.0),
                low=_number(row[3]),
                high=_number(row[2]),
            )
        )
    return sorted(samples, key=lambda item: item.at)


def calculate_excursion_metrics(
    cycle: list[dict],
    klines: Iterable[list],
    stored_initial_risk: Optional[float] = None,
    stored_stop_price: Optional[float] = None,
) -> dict:
    if not cycle:
        raise ExcursionCalculationError("成交周期为空")
    position_side = order_position_side(cycle[0])
    if position_side not in ("LONG", "SHORT"):
        raise ExcursionCalculationError("无法识别持仓方向")
    sign = 1.0 if position_side == "LONG" else -1.0
    start_at, end_at = order_time(cycle[0]), order_time(cycle[-1])

    events: list[tuple[datetime, int, object]] = []
    for sample in parse_kline_samples(klines):
        if start_at <= sample.at <= end_at:
            events.append((sample.at, 0, sample))
    for row in cycle:
        events.append((order_time(row), 1, row))
    events.sort(key=lambda item: (item[0], item[1]))

    quantity = 0.0
    average_entry = 0.0
    realized = 0.0
    fees = 0.0
    max_equity = 0.0
    min_equity = 0.0
    max_at = start_at
    min_at = start_at

    def sample(price: float, at: datetime) -> None:
        nonlocal max_equity, min_equity, max_at, min_at
        equity = realized + sign * (price - average_entry) * quantity - fees
        if equity > max_equity:
            max_equity, max_at = equity, at
        if equity < min_equity:
            min_equity, min_at = equity, at

    for at, kind, payload in events:
        if kind == 0:
            bar = payload
            sample(bar.low, at)
            sample(bar.high, at)
            continue

        row = payload
        price = order_price(row)
        qty = order_quantity(row)
        if price <= 0 or qty <= EPSILON:
            continue
        if quantity > EPSILON:
            sample(price, at)
        fees += abs(_number(row.get("commission")))
        direction = str(row.get("trade_direction") or "").upper()
        if direction == "OPEN":
            new_qty = quantity + qty
            average_entry = ((average_entry * quantity) + (price * qty)) / new_qty
            quantity = new_qty
        elif direction == "CLOSE" and quantity > EPSILON:
            close_qty = min(quantity, qty)
            stored_realized = row.get("realized_pnl")
            if stored_realized is None:
                realized += sign * (price - average_entry) * close_qty
            else:
                realized += _number(stored_realized)
            quantity = max(0.0, quantity - close_qty)
            if quantity <= EPSILON:
                quantity = 0.0
                average_entry = 0.0
        if quantity > EPSILON:
            sample(price, at)
        else:
            final_equity = realized - fees
            if final_equity > max_equity:
                max_equity, max_at = final_equity, at
            if final_equity < min_equity:
                min_equity, min_at = final_equity, at

    if quantity > EPSILON:
        raise ExcursionCalculationError("目标成交周期尚未完全平仓")

    net_pnl = realized - fees
    mfe = max(0.0, max_equity)
    mae = max(0.0, -min_equity)
    planned_stop = _number(stored_stop_price) or next(
        (_number(row.get("sl_price")) for row in cycle if _number(row.get("sl_price")) > 0),
        0.0,
    )
    initial_risk = _number(stored_initial_risk)
    if initial_risk <= EPSILON and planned_stop > 0:
        initial_risk = sum(
            abs(order_price(row) - planned_stop) * order_quantity(row)
            + abs(_number(row.get("commission")))
            for row in cycle
            if str(row.get("trade_direction") or "").upper() == "OPEN"
        )
    if initial_risk <= EPSILON:
        initial_risk = 0.0

    giveback = max(0.0, mfe - net_pnl)
    return {
        "planned_stop_price": planned_stop or None,
        "initial_risk_usdc": initial_risk or None,
        "mfe_usdc": mfe,
        "mae_usdc": mae,
        "mfe_at": max_at,
        "mae_at": min_at,
        "net_pnl": net_pnl,
        "mfe_r": mfe / initial_risk if initial_risk else None,
        "mae_r": mae / initial_risk if initial_risk else None,
        "net_pnl_r": net_pnl / initial_risk if initial_risk else None,
        "profit_capture_rate": min(1.0, max(0.0, net_pnl) / mfe) if mfe > EPSILON else None,
        "exit_efficiency": net_pnl / mfe if mfe > EPSILON else None,
        "profit_giveback_usdc": giveback,
        "profit_giveback_rate": giveback / mfe if mfe > EPSILON else None,
        "excursion_source": "1m_kline",
        "excursion_version": ALGORITHM_VERSION,
    }


def fetch_cycle_klines(client, symbol: str, start_at: datetime, end_at: datetime) -> list:
    """分页拉取完整持仓区间的 1m K 线。"""
    cursor = int(_timestamp(start_at).replace(tzinfo=timezone.utc).timestamp() * 1000) - 60_000
    end_ms = int(_timestamp(end_at).replace(tzinfo=timezone.utc).timestamp() * 1000) + 60_000
    rows: list = []
    while cursor <= end_ms:
        page = client.get_kline_data(
            symbol=symbol,
            interval="1m",
            limit=1500,
            start_time=cursor,
            end_time=end_ms,
        ) or []
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1][0]) + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(page) < 1500:
            break
    return rows

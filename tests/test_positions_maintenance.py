import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.routers import positions as positions_router


def test_backfill_maintenance_is_scoped_to_current_user(monkeypatch):
    calls = []
    async def run_inline(task_name, user_id, operation):
        return operation()
    monkeypatch.setattr(positions_router, "_run_position_maintenance", run_inline)
    monkeypatch.setattr(
        positions_router,
        "_repair_missing_order_links",
        lambda limit, user_id=None: calls.append((limit, user_id)) or (3, 1),
    )

    result = asyncio.run(positions_router.backfill_position_open_orders(
        positions_router.PositionMaintenanceIn(),
        {"sub": "5", "username": "Will", "role": "user"},
    ))

    assert result == {"repaired": 3, "skipped": 1}
    assert calls == [(10000, 5)]


def test_admin_can_run_mfe_maintenance_for_selected_user(monkeypatch):
    async def run_inline(task_name, user_id, operation):
        return operation()
    monkeypatch.setattr(positions_router, "_run_position_maintenance", run_inline)
    monkeypatch.setattr(
        positions_router.db_module,
        "get_user_by_username",
        lambda username: {"id": 7, "username": username},
    )
    calls = []
    monkeypatch.setattr(
        positions_router,
        "recalculate_missing_metrics",
        lambda **kwargs: calls.append(kwargs) or {
            "scanned": 2,
            "calculated": 2,
            "queued": 0,
            "failed": 0,
            "duplicate_history_rows": 0,
        },
    )

    result = asyncio.run(positions_router.recalculate_position_mfe(
        positions_router.PositionMaintenanceIn(username="alice"),
        {"sub": "1", "username": "admin", "role": "admin"},
    ))

    assert result["calculated"] == 2
    assert calls == [{"user_id": 7, "dry_run": False}]


def test_non_admin_cannot_maintain_another_user():
    with pytest.raises(HTTPException) as exc_info:
        positions_router._resolve_maintenance_user_id(
            {"sub": "5", "username": "Will", "role": "user"},
            "alice",
        )

    assert exc_info.value.status_code == 403


def test_position_review_is_loaded_for_position_owner(monkeypatch):
    monkeypatch.setattr(
        positions_router.db_module,
        "get_position_by_id",
        lambda position_id: {"id": position_id, "user_id": 5},
    )
    monkeypatch.setattr(
        positions_router.db_module,
        "get_position_review",
        lambda position_id, user_id: {
            "id": 9,
            "position_id": position_id,
            "user_id": user_id,
            "market_state": "TREND",
            "setup_name": "pullback",
            "is_planned_trade": 1,
            "created_at": datetime(2026, 9, 12, 8, 0),
            "updated_at": datetime(2026, 9, 12, 8, 1),
        },
    )

    result = positions_router.get_position_review(
        41,
        {"sub": "5", "username": "Will", "role": "user"},
    )

    assert result.position_id == 41
    assert result.user_id == 5
    assert result.market_state == "TREND"
    assert result.is_planned_trade is True


def test_position_review_cannot_be_accessed_by_another_user(monkeypatch):
    monkeypatch.setattr(
        positions_router.db_module,
        "get_position_by_id",
        lambda position_id: {"id": position_id, "user_id": 8},
    )

    with pytest.raises(HTTPException) as exc_info:
        positions_router.get_position_review(
            41,
            {"sub": "5", "username": "Will", "role": "user"},
        )

    assert exc_info.value.status_code == 403


def test_admin_saves_review_under_position_owner(monkeypatch):
    monkeypatch.setattr(
        positions_router.db_module,
        "get_position_by_id",
        lambda position_id: {"id": position_id, "user_id": 8},
    )
    calls = []

    def upsert(position_id, user_id, values):
        calls.append((position_id, user_id, values))
        return {
            "id": 10,
            "position_id": position_id,
            "user_id": user_id,
            **values,
            "created_at": datetime(2026, 9, 12, 8, 0),
            "updated_at": datetime(2026, 9, 12, 8, 1),
        }

    monkeypatch.setattr(positions_router.db_module, "upsert_position_review", upsert)
    body = positions_router.PositionReviewIn(
        setup_name="  trend pullback  ",
        opportunity_grade="A",
        is_planned_trade=True,
    )

    result = positions_router.save_position_review(
        41,
        body,
        {"sub": "1", "username": "admin", "role": "admin"},
    )

    assert result.user_id == 8
    assert calls[0][0:2] == (41, 8)
    assert calls[0][2]["setup_name"] == "trend pullback"

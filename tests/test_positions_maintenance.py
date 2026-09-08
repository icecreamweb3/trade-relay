import asyncio

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

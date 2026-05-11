"""
Users router: admin CRUD for user accounts.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from trade_relay import database as db_module
from trade_relay.auth.manager import (
    Session, hash_password,
    create_user as mgr_create_user,
    delete_user as mgr_delete_user,
    update_user as mgr_update_user,
    reset_user_password as mgr_reset_password,
)
from backend.routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/users", tags=["users"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    binance_api_key: str = ""
    binance_api_secret: str = ""

class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    binance_api_key: Optional[str] = None
    binance_api_secret: Optional[str] = None
    is_active: Optional[bool] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserOut])
def list_users(admin: dict = Depends(require_admin)):
    rows = db_module.get_all_users()
    return [UserOut(id=r["id"], username=r["username"], role=r["role"], is_active=bool(r["is_active"])) for r in rows]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: CreateUserRequest, admin: dict = Depends(require_admin)):
    session = Session(int(admin["sub"]), admin["username"], admin["role"])
    ok, msg = mgr_create_user(
        session,
        body.username,
        body.password,
        body.role,
        body.binance_api_key,
        body.binance_api_secret,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    row = db_module.get_user_by_username(body.username)
    return UserOut(id=row["id"], username=row["username"], role=row["role"], is_active=bool(row["is_active"]))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UpdateUserRequest, admin: dict = Depends(require_admin)):
    row = db_module.get_user_by_id(user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    if body.password is not None:
        db_module.update_user_password(user_id, hash_password(body.password))

    if body.role is not None:
        db_module.update_user_role(user_id, body.role)

    if body.binance_api_key is not None or body.binance_api_secret is not None:
        db_module.update_user_api_credentials(
            user_id,
            body.binance_api_key or "",
            body.binance_api_secret or "",
        )

    if body.is_active is not None:
        if body.is_active:
            db_module.activate_user(user_id)
        else:
            db_module.deactivate_user(user_id)

    row = db_module.get_user_by_id(user_id)
    return UserOut(id=row["id"], username=row["username"], role=row["role"], is_active=bool(row["is_active"]))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    session = Session(int(admin["sub"]), admin["username"], admin["role"])
    ok, msg = mgr_delete_user(session, user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

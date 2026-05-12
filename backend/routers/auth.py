"""
Auth router: POST /api/auth/login, GET /api/auth/me, POST /api/auth/change-password
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from trade_relay.auth.manager import Session, change_own_password, login as auth_login
from backend.config import JWT_SECRET, JWT_ALGO, JWT_EXPIRE_HOURS
from backend.logger import get_logger

router = APIRouter(prefix="/api/auth", tags=["auth"])
_log = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)


# ── Schemas ───────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return payload


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    _log.info("Login attempt: username=%s", body.username)
    session = auth_login(body.username, body.password)
    if session is None:
        _log.warning("Login failed: username=%s", body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    import trade_relay.database as db_module
    row = db_module.get_user_by_id(session.user_id)
    _log.info("Login success: user_id=%s username=%s role=%s", session.user_id, session.username, session.role)
    token = create_token(session.user_id, session.username, session.role)
    return LoginResponse(
        access_token=token,
        user=UserInfo(
            id=session.user_id,
            username=session.username,
            role=session.role,
            is_active=bool(row.get("is_active", 1)) if row else True,
        ),
    )


@router.get("/me", response_model=UserInfo)
def get_me(user: dict = Depends(get_current_user)):
    import trade_relay.database as db_module
    row = db_module.get_user_by_id(int(user["sub"]))
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserInfo(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        is_active=bool(row["is_active"]),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    session = Session(int(user["sub"]), user["username"], user["role"])
    ok, msg = change_own_password(session, body.current_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return None

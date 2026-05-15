"""
Authentication manager: password hashing, login, user management.
Default admin is created on first run with credentials: admin / Admin@123
"""
from typing import Optional
import bcrypt
from trade_relay import database as db
from trade_relay.i18n import t


# ──────────────────────────────────────────────
# Session: lightweight in-process session state
# ──────────────────────────────────────────────

class Session:
    def __init__(self, user_id: int, username: str, role: str):
        self.user_id = user_id
        self.username = username
        self.role = role  # 'admin' | 'user'

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def __repr__(self) -> str:
        return f"<Session user={self.username} role={self.role}>"


# ──────────────────────────────────────────────
# Password helpers
# ──────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return bcrypt hash of the password as a UTF-8 string."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except Exception:
        return False


# ──────────────────────────────────────────────
# Auth operations
# ──────────────────────────────────────────────

def login(username: str, password: str) -> Optional[Session]:
    """
    Attempt login. Returns Session on success, None on failure.
    """
    if not username or not password:
        return None
    row = db.get_user_by_username(username)
    if row is None:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    db.log_operation(row["id"], username, "LOGIN", "Successful login")
    return Session(user_id=row["id"], username=row["username"], role=row["role"])


# ──────────────────────────────────────────────
# User management (admin only)
# ──────────────────────────────────────────────

def create_user(
    session: Session,
    username: str,
    password: str,
    role: str = "user",
    binance_api_key: str = "",
    binance_api_secret: str = "",
) -> tuple[bool, str]:
    """
    Create a new user. Only admins may call this.
    Returns (success, message).
    """
    if not session.is_admin:
        return False, t("unauthorized")
    if not username or not password:
        return False, t("field_required", "username/password")

    password_hash = hash_password(password)
    user_id = db.create_user(username, password_hash, role, binance_api_key, binance_api_secret)
    if user_id is None:
        return False, t("user_exists", username)

    db.log_operation(session.user_id, session.username, "CREATE_USER",
                     f"Created user '{username}' with role '{role}'")
    return True, t("user_created", username)


def delete_user(session: Session, target_user_id: int) -> tuple[bool, str]:
    """Deactivate a user. Only admins may call this."""
    if not session.is_admin:
        return False, t("unauthorized")
    if target_user_id == session.user_id:
        return False, t("cannot_delete_self")

    target = db.get_user_by_id(target_user_id)
    if target is None:
        return False, t("error")
    if target["role"] == "admin":
        return False, t("cannot_delete_admin")

    db.deactivate_user(target_user_id)
    db.log_operation(session.user_id, session.username, "DELETE_USER",
                     f"Deactivated user '{target['username']}'")
    return True, t("user_deleted", target["username"])


def update_user(
    session: Session,
    target_user_id: int,
    role: str,
    binance_api_key: str = "",
    binance_api_secret: str = "",
) -> tuple[bool, str]:
    """Update a user's role and API credentials. Only admins may call this."""
    if not session.is_admin:
        return False, t("unauthorized")

    target = db.get_user_by_id(target_user_id)
    if target is None:
        return False, t("error")

    db.update_user_role(target_user_id, role)
    db.update_user_api_credentials(target_user_id, binance_api_key, binance_api_secret)
    db.log_operation(session.user_id, session.username, "UPDATE_USER",
                     f"Updated user '{target['username']}' role='{role}'")
    return True, t("user_updated", target["username"])


def reset_user_password(
    session: Session,
    target_user_id: int,
    new_password: str,
) -> tuple[bool, str]:
    """Reset another user's password. Only admins may call this."""
    if not session.is_admin:
        return False, t("unauthorized")
    if not new_password:
        return False, t("field_required", "new_password")

    target = db.get_user_by_id(target_user_id)
    if target is None:
        return False, t("error")

    password_hash = hash_password(new_password)
    db.update_user_password(target_user_id, password_hash)
    db.log_operation(session.user_id, session.username, "RESET_PASSWORD",
                     f"Reset password for user '{target['username']}'")
    return True, t("password_reset", target["username"])


def change_own_password(
    session: Session,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    """Change the current user's password after verifying the old password."""
    if not current_password or not new_password:
        return False, "Current password and new password are required"

    row = db.get_user_by_id(session.user_id)
    if row is None:
        return False, "User not found"

    if not verify_password(current_password, row["password_hash"]):
        return False, "Current password is incorrect"

    password_hash = hash_password(new_password)
    db.update_user_password(session.user_id, password_hash)
    db.log_operation(session.user_id, session.username, "CHANGE_PASSWORD",
                     "Changed own password")
    return True, "Password updated"


# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────

import os as _os

_DEFAULT_ADMIN_USERNAME = "admin"
_DEFAULT_ADMIN_PASSWORD = "Admin@123"


def ensure_admin_exists() -> None:
    """Create the default admin account if no admin exists.

    Credentials are read from env vars TRADE_RELAY_ADMIN_USERNAME /
    TRADE_RELAY_ADMIN_PASSWORD (set them in .env.production), falling back to
    the built-in defaults.
    """
    username = _os.environ.get("TRADE_RELAY_ADMIN_USERNAME", "").strip() or _DEFAULT_ADMIN_USERNAME
    password = _os.environ.get("TRADE_RELAY_ADMIN_PASSWORD", "").strip() or _DEFAULT_ADMIN_PASSWORD
    existing = db.get_user_by_username(username)
    if existing is None:
        password_hash = hash_password(password)
        db.create_user(username, password_hash, "admin")
        db.log_operation(None, "system", "BOOTSTRAP",
                         f"Default admin account created: {DEFAULT_ADMIN_USERNAME}")

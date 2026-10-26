"""Admin-only server log discovery, preview, and download endpoints."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.logger import get_log_dir
from backend.routers.auth import require_admin


router = APIRouter(prefix="/api/server-logs", tags=["server-logs"])

_LOG_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.log(?:\.\d+)?$")
_DEFAULT_PREVIEW_BYTES = 512 * 1024
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024


class ServerLogFileOut(BaseModel):
    name: str
    size: int
    modified_at: str


class ServerLogPreviewOut(ServerLogFileOut):
    content: str
    truncated: bool


def _serialize_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_log_file(filename: str) -> Path:
    """Resolve one plain log filename without allowing traversal or symlinks."""
    if not _LOG_FILE_NAME.fullmatch(filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found")

    root = get_log_dir().resolve()
    candidate = root / filename
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found") from None

    if candidate.is_symlink() or resolved.parent != root or not resolved.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log file not found")
    return resolved


@router.get("", response_model=list[ServerLogFileOut])
def list_server_logs(_admin: dict = Depends(require_admin)):
    log_dir = get_log_dir()
    if not log_dir.exists() or not log_dir.is_dir():
        return []

    files: list[ServerLogFileOut] = []
    for candidate in log_dir.iterdir():
        if candidate.is_symlink() or not candidate.is_file() or not _LOG_FILE_NAME.fullmatch(candidate.name):
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        files.append(ServerLogFileOut(
            name=candidate.name,
            size=stat.st_size,
            modified_at=_serialize_mtime(stat.st_mtime),
        ))
    return sorted(files, key=lambda item: (item.modified_at, item.name), reverse=True)


@router.get("/{filename}/download")
def download_server_log(filename: str, _admin: dict = Depends(require_admin)):
    path = _resolve_log_file(filename)
    return FileResponse(
        path=path,
        media_type="text/plain; charset=utf-8",
        filename=path.name,
    )


@router.get("/{filename}", response_model=ServerLogPreviewOut)
def preview_server_log(
    filename: str,
    max_bytes: int = Query(_DEFAULT_PREVIEW_BYTES, ge=1024, le=_MAX_PREVIEW_BYTES),
    _admin: dict = Depends(require_admin),
):
    path = _resolve_log_file(filename)
    stat = path.stat()
    start = max(0, stat.st_size - max_bytes)
    with path.open("rb") as handle:
        handle.seek(start)
        content = handle.read(max_bytes).decode("utf-8", errors="replace")

    return ServerLogPreviewOut(
        name=path.name,
        size=stat.st_size,
        modified_at=_serialize_mtime(stat.st_mtime),
        content=content,
        truncated=start > 0,
    )

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.routers import server_logs
from backend.routers.auth import get_current_user, require_admin


ADMIN = {"sub": "1", "username": "admin", "role": "admin"}


def test_server_log_routes_reject_unauthenticated_and_non_admin_users():
    for endpoint in (
        server_logs.list_server_logs,
        server_logs.preview_server_log,
        server_logs.download_server_log,
    ):
        dependency = inspect.signature(endpoint).parameters["_admin"].default.dependency
        assert dependency is require_admin

    with pytest.raises(HTTPException) as unauthenticated:
        get_current_user(None)
    assert unauthenticated.value.status_code == 401

    with pytest.raises(HTTPException) as forbidden:
        require_admin({"sub": "2", "username": "trader", "role": "user"})
    assert forbidden.value.status_code == 403


def test_list_server_logs_only_returns_regular_log_files_sorted_newest(monkeypatch, tmp_path):
    older = tmp_path / "backend_20261026_100000.log"
    newer = tmp_path / "backend_20261026_110000.log"
    ignored = tmp_path / "notes.txt"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    ignored.write_text("ignore", encoding="utf-8")
    older.touch()
    newer.touch()
    older_stat = older.stat()
    newer_stat = newer.stat()
    # Make ordering deterministic without depending on filesystem timestamp resolution.
    import os
    os.utime(older, (older_stat.st_atime, 100))
    os.utime(newer, (newer_stat.st_atime, 200))
    monkeypatch.setattr(server_logs, "get_log_dir", lambda: tmp_path)

    result = server_logs.list_server_logs(ADMIN)

    assert [item.name for item in result] == [newer.name, older.name]
    assert result[0].size == 3


def test_preview_server_log_returns_tail_and_truncation_flag(monkeypatch, tmp_path):
    log_file = tmp_path / "backend_20261026_110000.log"
    log_file.write_text("A" * 1024 + "LATEST", encoding="utf-8")
    monkeypatch.setattr(server_logs, "get_log_dir", lambda: tmp_path)

    result = server_logs.preview_server_log(log_file.name, max_bytes=1024, _admin=ADMIN)

    assert result.content.endswith("LATEST")
    assert len(result.content.encode("utf-8")) == 1024
    assert result.truncated is True


def test_download_server_log_returns_file_response(monkeypatch, tmp_path):
    log_file = tmp_path / "backend_20261026_110000.log.1"
    log_file.write_text("server log", encoding="utf-8")
    monkeypatch.setattr(server_logs, "get_log_dir", lambda: tmp_path)

    result = server_logs.download_server_log(log_file.name, ADMIN)

    assert isinstance(result, FileResponse)
    assert Path(result.path) == log_file
    assert result.filename == log_file.name


@pytest.mark.parametrize("filename", ["../secret.log", "nested/file.log", "notes.txt"])
def test_log_file_resolution_rejects_non_log_and_traversal(monkeypatch, tmp_path, filename):
    monkeypatch.setattr(server_logs, "get_log_dir", lambda: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        server_logs.preview_server_log(filename, max_bytes=1024, _admin=ADMIN)

    assert exc_info.value.status_code == 404

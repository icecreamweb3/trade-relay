"""
Trade Relay — Backend Logging Setup

每次启动以时间戳命名日志文件，单文件最大 100 MB，自动滚动。
格式: 时间 | 级别 | 文件名:行号 | 函数名 | 消息
"""

import logging
import logging.handlers
import os
import re
import time
from datetime import datetime
from pathlib import Path


# Suppress urllib3 connectionpool DEBUG noise for routine polling endpoints
# (account & positionRisk) when they return HTTP 200.  Error responses are
# still allowed through so failures remain visible.
_QUIET_PATTERN = re.compile(
    r'"GET /fapi/v2/(account|positionRisk)\?.*HTTP/\d\.\d"\s+200\b'
)


class _SuppressRoutinePollingFilter(logging.Filter):
    """Drop urllib3 DEBUG records for successful account/positionRisk polls."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.DEBUG and record.name.startswith("urllib3"):
            msg = record.getMessage()
            if _QUIET_PATTERN.search(msg):
                return False
        return True

# 日志目录: 默认 <project_root>/logs/，Docker 中默认 /app/logs，可通过
# TRADE_RELAY_LOG_DIR 覆盖。
def _resolve_log_dir() -> Path:
    explicit = os.getenv("TRADE_RELAY_LOG_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    default_dir = Path(__file__).parent.parent / "logs"
    if Path("/app").exists():
        return Path("/app/logs")
    return default_dir


_LOG_DIR = _resolve_log_dir()
_MAX_BYTES = 100 * 1024 * 1024   # 100 MB
_BACKUP_COUNT = 9                  # 最多保留 9 个滚动备份


def setup_logging(level: int = logging.DEBUG) -> logging.Logger:
    """
    初始化全局日志配置，返回根 logger。
    在 FastAPI lifespan 启动时调用一次即可。
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = _LOG_DIR / f"backend_{timestamp}.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | %(levelname)-8s | %(filename)s:%(lineno)d | %(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fmt.converter = time.localtime  # 使用本地时间而非 UTC

    # 滚动文件 handler（达到 100 MB 后自动创建 .1/.2/… 备份）
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    file_handler.addFilter(_SuppressRoutinePollingFilter())

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(_SuppressRoutinePollingFilter())

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler（uvicorn --reload 会多次导入）
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    else:
        # 覆盖已有 handler 保证文件名是本次启动的时间戳
        root.handlers.clear()
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    root.info("Logging initialised → %s", log_file)

    # ── Patch uvicorn access log to include timestamps ──────────────────────
    # uvicorn.access uses its own propagation-off logger by default; we attach
    # a formatter that matches our application log style.
    access_fmt = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d | ACCESS   | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    access_fmt.converter = time.localtime

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_handler = logging.StreamHandler()
    access_handler.setFormatter(access_fmt)
    access_logger.addHandler(access_handler)
    access_logger.addHandler(file_handler)
    access_logger.propagate = False

    return root


def get_logger(name: str) -> logging.Logger:
    """获取子 logger，各模块调用: logger = get_logger(__name__)"""
    return logging.getLogger(name)

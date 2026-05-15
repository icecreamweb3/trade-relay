#!/usr/bin/env python3
"""Trade Relay backend launcher for the Electron desktop client."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from trade_relay.env_loader import load_env


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    load_env(ROOT, override=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Trade Relay FastAPI backend")
    parser.add_argument("--host", default=os.environ.get("BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BACKEND_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn autoreload")
    parser.add_argument("--log-level", default=os.environ.get("BACKEND_LOG_LEVEL", "info"))
    return parser


def main() -> None:
    _load_env()
    args = _build_parser().parse_args()
    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()

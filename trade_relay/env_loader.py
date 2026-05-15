from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


ENV_FILE_NAMES: tuple[str, ...] = (".env.production", ".env")


def iter_env_candidates(root: Path | None = None) -> Iterable[Path]:
    base = root or Path(__file__).resolve().parent.parent
    for name in ENV_FILE_NAMES:
        yield base / name


def resolve_env_file(root: Path | None = None) -> Path | None:
    for candidate in iter_env_candidates(root):
        if candidate.exists():
            return candidate
    return None


def load_env(root: Path | None = None, override: bool = False) -> Path | None:
    env_path = resolve_env_file(root)
    if env_path is None:
        return None

    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_path, override=override)
        return env_path
    except ImportError:
        with open(env_path, encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if override:
                    os.environ[key.strip()] = value.strip()
                else:
                    os.environ.setdefault(key.strip(), value.strip())
        return env_path
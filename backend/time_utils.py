from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def serialize_utc_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None

    dt: datetime | None = None

    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) >= 1e11:
            timestamp /= 1000.0
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    elif isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        if trimmed.isdigit():
            return serialize_utc_timestamp(int(trimmed))

        normalized = trimmed.replace(' ', 'T')
        try:
            if normalized.endswith('Z'):
                dt = datetime.fromisoformat(normalized[:-1] + '+00:00')
            else:
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return trimmed
    else:
        return str(value)

    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def serialize_utc_timestamp_required(value: Any) -> str:
    return serialize_utc_timestamp(value) or ''
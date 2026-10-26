"""Znaczniki czasu z dzienników (ISO 8601 z „Z”) jako sekundy epoki."""
from datetime import datetime, timezone


def parse_ts(value) -> float:
    if not value or not isinstance(value, str):
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(ts: float) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

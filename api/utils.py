"""Shared API utilities."""

from datetime import datetime


def utc_iso(dt: datetime | None) -> str | None:
    """Convert datetime to UTC ISO 8601 string suitable for frontend parsing.

    SQLite + SQLAlchemy may strip timezone info during round-trip even with
    DateTime(timezone=True).  This function ensures the output always carries
    +00:00 suffix so that JavaScript new Date() correctly interprets the
    value as UTC rather than local time.

    Usage (replace all .isoformat() calls in API serializers):
        "created_at": utc_iso(obj.created_at),
    """
    if dt is None:
        return None
    s = dt.isoformat()
    if dt.tzinfo is None:
        s += "+00:00"
    return s

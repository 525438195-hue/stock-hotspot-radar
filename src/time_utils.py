"""Time formatting helpers for Chinese-facing outputs."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo is expected in modern Python.
    ZoneInfo = None  # type: ignore


BEIJING_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))
UTC = timezone.utc


def format_publish_time(value: object) -> str:
    """Return a readable Beijing-time timestamp, or a safe fallback."""
    text = str(value or "").strip()
    if not text:
        return "时间未知"
    parsed = _parse_datetime(text)
    if parsed is None:
        return "时间未知" if _looks_like_english_time(text) else text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def format_publish_time_iso(value: object) -> str:
    """Return an ISO timestamp with Beijing timezone for internal scoring."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_datetime(text)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(BEIJING_TZ).isoformat(timespec="seconds")


def _parse_datetime(text: str) -> datetime | None:
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _looks_like_english_time(text: str) -> bool:
    return bool(
        re.search(
            r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|GMT|UTC)\b",
            text,
            flags=re.IGNORECASE,
        )
    )

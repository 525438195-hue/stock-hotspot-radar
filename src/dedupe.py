"""Simple deterministic de-duplication for simulated events."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any


_NOISE_WORDS = [
    "网传",
    "消息称",
    "关注",
    "升温",
    "明显",
    "推进",
    "披露",
]


def _normalize_text(value: str) -> str:
    value = value.lower()
    for word in _NOISE_WORDS:
        value = value.replace(word.lower(), "")
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def _dedupe_key(event: dict[str, Any]) -> str:
    topic = str(event.get("topic_hint", ""))
    tickers = ",".join(sorted(event.get("tickers", [])))
    normalized_title = _normalize_text(str(event.get("title", "")))
    if tickers:
        return f"{topic}|{tickers}|{normalized_title[:16]}"
    return f"{topic}|{normalized_title[:20]}"


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    for event in events:
        key = _dedupe_key(event)
        if key not in grouped:
            grouped[key] = {
                **event,
                "duplicate_count": 1,
                "related_event_ids": [event["event_id"]],
                "related_sources": [event["source"]],
            }
            continue

        current = grouped[key]
        current["duplicate_count"] += 1
        current["related_event_ids"].append(event["event_id"])
        if event["source"] not in current["related_sources"]:
            current["related_sources"].append(event["source"])

    return list(grouped.values())

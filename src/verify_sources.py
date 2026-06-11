"""Source verification and fact-status rules."""

from __future__ import annotations

from typing import Any


def source_type_config(source_type: str, sources_config: dict[str, Any]) -> dict[str, Any]:
    return dict(sources_config.get("source_types", {}).get(source_type, {}))


def fact_status_for_event(event: dict[str, Any], sources_config: dict[str, Any]) -> str:
    source_type = str(event.get("source_type", ""))
    config = source_type_config(source_type, sources_config)
    role = config.get("evidence_role")
    if role in {"confirmed_fact", "unverified_rumor", "market_reaction"}:
        return str(role)
    return "inference"


def is_highest_priority_source(source_type: str, sources_config: dict[str, Any]) -> bool:
    return source_type in set(sources_config.get("highest_priority_source_types", []))


def is_sentiment_only_source(source_type: str, sources_config: dict[str, Any]) -> bool:
    return source_type in set(sources_config.get("sentiment_only_source_types", []))


def source_base_confidence(source_type: str, sources_config: dict[str, Any]) -> int:
    return int(source_type_config(source_type, sources_config).get("base_confidence", 50))

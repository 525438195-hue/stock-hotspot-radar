"""Build coverage reports for configured search sources."""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlparse


def build_coverage_report(
    queries: list[str],
    source_status: list[dict[str, Any]],
    raw_results: list[dict[str, Any]],
    deduped_results: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    statuses = {status.get("status", "") for status in source_status}
    source_type_counts = Counter(str(item.get("source_type", "未知来源")) for item in deduped_results)
    domains = {_domain(str(item.get("url", ""))) for item in deduped_results if item.get("url")}
    domains.discard("")

    official_sources_checked = [
        _status_name(status)
        for status in source_status
        if "公告" in _status_name(status) or "official" in str(status.get("source_type", ""))
    ]

    return {
        "searched_queries_count": len(queries),
        "successful_sources": [_status_name(status) for status in source_status if status.get("status") == "success"],
        "failed_sources": [_status_name(status) for status in source_status if status.get("status") == "failed"],
        "timeout_sources": [_status_name(status) for status in source_status if status.get("status") == "timeout"],
        "skipped_sources": [_status_name(status) for status in source_status if status.get("status") == "skipped"],
        "placeholder_sources": [_status_name(status) for status in source_status if status.get("status") == "placeholder"],
        "fallback_sources": [_status_name(status) for status in source_status if status.get("status") == "fallback"],
        "source_type_coverage": dict(source_type_counts),
        "unique_domains_count": len(domains),
        "raw_results_count": len(raw_results),
        "deduped_results_count": len(deduped_results),
        "official_sources_checked": official_sources_checked,
        "warnings": list(warnings or []),
        "fallback_usage": [],
        "has_success": "success" in statuses,
        "source_status": list(source_status),
    }


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.")


def _status_name(status: dict[str, Any]) -> str:
    return str(status.get("source_name") or status.get("source") or "")

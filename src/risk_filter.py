"""Risk flag rules for events and announcements."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from verify_sources import is_sentiment_only_source


HYPE_KEYWORDS = ["翻倍", "爆拉", "一字板", "重磅利好", "稳赚"]


def _combined_text(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')}\n{item.get('content', '')}"


def _append_unique(flags: list[dict[str, str]], risk_type: str, reason: str, severity: str) -> None:
    if any(flag["risk_type"] == risk_type for flag in flags):
        return
    flags.append({"risk_type": risk_type, "reason": reason, "severity": severity})


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_stale(publish_time: str, as_of: str | None = None, max_age_days: int = 7) -> bool:
    published = _parse_time(publish_time)
    if published is None:
        return True

    if as_of:
        current = _parse_time(as_of)
    else:
        current = datetime.now(tz=published.tzinfo) if published.tzinfo else datetime.now()
    if current is None:
        return False
    return (current - published).days > max_age_days


def related_announcements_for_event(
    event: dict[str, Any],
    announcements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tickers = set(event.get("tickers", []))
    if not tickers:
        return []
    return [item for item in announcements if item.get("ticker") in tickers]


def announcement_risk_flags(announcement: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    announcement_type = str(announcement.get("announcement_type", ""))

    if announcement_type == "clarification_no_business":
        _append_unique(
            flags,
            "clarification_no_business",
            "公司公告澄清不涉及相关业务，公告优先于新闻或传闻。",
            "high",
        )
    elif announcement_type == "shareholder_reduction":
        _append_unique(flags, "shareholder_reduction", "公告出现股东减持计划。", "medium")
    elif announcement_type == "regulatory_letter":
        _append_unique(flags, "regulatory_letter", "交易所或监管机构要求公司说明相关事项。", "high")
    elif announcement_type == "performance_forecast":
        text = _combined_text(announcement)
        if any(word in text for word in ["下降", "亏损", "下滑", "减少"]):
            _append_unique(flags, "performance_pressure", "业绩预告存在经营压力表述。", "medium")

    return flags


def event_risk_flags(
    event: dict[str, Any],
    announcements: list[dict[str, Any]] | None = None,
    sources_config: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    source_type = str(event.get("source_type", ""))
    text = _combined_text(event)

    if sources_config and is_sentiment_only_source(source_type, sources_config):
        _append_unique(
            flags,
            "low_quality_source",
            "社媒、股吧或截图类来源只能作为情绪信号，不能作为事实依据。",
            "high",
        )

    if source_type == "screenshot" or "截图" in text:
        _append_unique(flags, "screenshot_only", "信息依赖截图，缺少可核验原始披露链接。", "high")

    if source_type in {"social_media", "guba", "screenshot"}:
        _append_unique(flags, "unverified_rumor", "尚未看到官方公告、交易所披露或政策文件确认。", "high")

    matched_hype = next((keyword for keyword in HYPE_KEYWORDS if keyword in text), None)
    if matched_hype:
        _append_unique(flags, "hype_language", f"文本包含情绪化表述“{matched_hype}”。", "medium")

    if _is_stale(str(event.get("publish_time", "")), as_of=as_of):
        _append_unique(flags, "stale_news", "发布时间超过7天或无法识别，需防范旧信息重复发酵。", "medium")

    if announcements:
        for announcement in related_announcements_for_event(event, announcements):
            announcement_type = announcement.get("announcement_type")
            if announcement_type == "clarification_no_business":
                _append_unique(
                    flags,
                    "announcement_conflict",
                    f"{announcement.get('company')}公告澄清不涉及相关业务，应以公告为准。",
                    "high",
                )
            for risk in announcement_risk_flags(announcement):
                _append_unique(flags, risk["risk_type"], risk["reason"], risk["severity"])

    return flags

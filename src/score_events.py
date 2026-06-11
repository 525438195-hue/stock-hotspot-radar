"""Transparent 100-point scoring model for hotspot events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from classify_topics import classify_topic
from risk_filter import event_risk_flags


OFFICIAL_SOURCE_CATEGORIES = {
    "official_announcement",
    "exchange_announcement",
    "government_policy",
}
SOCIAL_SOURCE_CATEGORIES = {"social_sentiment"}


def _sector_market(topic: str, market_data: dict[str, Any]) -> dict[str, Any]:
    for sector in market_data.get("sectors", []):
        if sector.get("sector") == topic:
            return dict(sector)
    return {}


def _bounded_score(score: int, rules: dict[str, Any]) -> int:
    bounds = rules.get("score_bounds", {})
    return max(int(bounds.get("min", 0)), min(int(bounds.get("max", 100)), score))


def _source_category(source_type: str, rules: dict[str, Any]) -> str:
    aliases = rules.get("source_type_aliases", {})
    return str(aliases.get(source_type, source_type if source_type in rules.get("base_scores", {}) else "unknown"))


def _base_score(source_type: str, rules: dict[str, Any]) -> int:
    category = _source_category(source_type, rules)
    return int(rules.get("base_scores", {}).get(category, rules.get("base_scores", {}).get("unknown", 0)))


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_old_news(event: dict[str, Any], market_data: dict[str, Any], rules: dict[str, Any]) -> bool:
    if event.get("old_news") is True:
        return True

    published = _parse_time(str(event.get("publish_time", "")))
    trade_date = market_data.get("trade_date")
    if published is None or not trade_date:
        return False

    as_of = _parse_time(f"{trade_date}T23:59:59+08:00")
    if as_of is None:
        return False
    return (as_of - published).days > int(rules.get("old_news_days", 7))


def _related_announcements(event: dict[str, Any], announcements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tickers = set(event.get("tickers", []))
    if not tickers:
        return []
    return [announcement for announcement in announcements if announcement.get("ticker") in tickers]


def _has_independent_sources(event: dict[str, Any]) -> bool:
    related_sources = {str(source) for source in event.get("related_sources", []) if source}
    return int(event.get("duplicate_count", 1)) >= 2 or len(related_sources) >= 2


def _has_official_confirmation(
    event: dict[str, Any],
    announcements: list[dict[str, Any]],
    rules: dict[str, Any],
) -> bool:
    category = _source_category(str(event.get("source_type", "")), rules)
    if category in OFFICIAL_SOURCE_CATEGORIES:
        return True

    confirmation_announcement_types = {"positive_business"}
    for announcement in _related_announcements(event, announcements):
        if announcement.get("announcement_type") not in confirmation_announcement_types:
            continue
        announcement_category = _source_category(str(announcement.get("source_type", "")), rules)
        if announcement_category in OFFICIAL_SOURCE_CATEGORIES:
            return True
    return False


def _market_volume_confirmed(market_signal: dict[str, Any], rules: dict[str, Any]) -> bool:
    thresholds = rules.get("market_thresholds", {})
    return float(market_signal.get("turnover_change_pct", 0)) >= float(
        thresholds.get("market_volume_confirmed_turnover_change_pct", 20.0)
    )


def _sector_strength_confirmed(market_signal: dict[str, Any], rules: dict[str, Any]) -> bool:
    thresholds = rules.get("market_thresholds", {})
    return (
        float(market_signal.get("change_pct", 0)) >= float(thresholds.get("sector_strength_change_pct", 3.0))
        or int(market_signal.get("limit_up_count", 0)) >= int(thresholds.get("sector_strength_limit_up_count", 5))
    )


def _high_position_chasing_risk(market_signal: dict[str, Any], rules: dict[str, Any]) -> bool:
    thresholds = rules.get("market_thresholds", {})
    return (
        float(market_signal.get("change_pct", 0)) >= float(thresholds.get("high_position_change_pct", 5.0))
        or int(market_signal.get("limit_up_count", 0)) >= int(thresholds.get("high_position_limit_up_count", 10))
    )


def _policy_continuity(event: dict[str, Any], rules: dict[str, Any]) -> bool:
    if event.get("policy_continuity") is True:
        return True
    return _source_category(str(event.get("source_type", "")), rules) == "government_policy"


def _add_flag(flags: list[dict[str, str]], risk_type: str, reason: str, severity: str = "medium") -> None:
    if any(flag["risk_type"] == risk_type for flag in flags):
        return
    flags.append({"risk_type": risk_type, "reason": reason, "severity": severity})


def _normalized_risk_flags(
    event: dict[str, Any],
    raw_flags: list[dict[str, str]],
    announcements: list[dict[str, Any]],
    market_signal: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    raw_risk_types = {flag["risk_type"] for flag in raw_flags}
    category = _source_category(str(event.get("source_type", "")), rules)
    related_announcements = _related_announcements(event, announcements)
    has_official = _has_official_confirmation(event, announcements, rules)

    if category in SOCIAL_SOURCE_CATEGORIES and not has_official:
        _add_flag(flags, "social_only", "仅来自社媒、股吧或截图类情绪信号，不能作为事实依据。", "high")

    if event.get("title_body_mismatch") is True:
        _add_flag(flags, "title_body_mismatch", "标题与正文核心信息不一致，需要人工复核。", "medium")

    if "stale_news" in raw_risk_types:
        _add_flag(flags, "old_news", "信息发布时间较旧，存在旧闻新炒风险。", "medium")

    for announcement in related_announcements:
        announcement_type = announcement.get("announcement_type")
        if announcement_type == "clarification_no_business":
            _add_flag(flags, "company_denial", "公司公告否认或澄清不涉及相关业务，以公告为准。", "high")
        elif announcement_type == "shareholder_reduction":
            _add_flag(flags, "reduction_announcement", "相关公司存在股东减持公告。", "medium")
        elif announcement_type == "regulatory_letter":
            _add_flag(flags, "regulatory_warning", "相关公司收到监管函或交易所问询。", "high")

    if "announcement_conflict" in raw_risk_types or "clarification_no_business" in raw_risk_types:
        _add_flag(flags, "company_denial", "公告信息与新闻或传闻冲突，以公告为准。", "high")
    if "shareholder_reduction" in raw_risk_types:
        _add_flag(flags, "reduction_announcement", "公告出现股东减持计划。", "medium")
    if "regulatory_letter" in raw_risk_types:
        _add_flag(flags, "regulatory_warning", "交易所或监管机构要求公司说明相关事项。", "high")

    if _high_position_chasing_risk(market_signal, rules):
        _add_flag(flags, "high_position_chasing_risk", "板块涨幅或涨停数量较高，需警惕高位追逐风险。", "medium")

    return flags


def _verification_status(
    event: dict[str, Any],
    risk_flags: list[dict[str, str]],
    bonuses_applied: set[str],
    rules: dict[str, Any],
) -> str:
    risk_types = {risk["risk_type"] for risk in risk_flags}
    category = _source_category(str(event.get("source_type", "")), rules)

    if "company_denial" in risk_types:
        return "contradicted"
    if "old_news" in risk_types:
        return "stale"
    if str(event.get("source_type", "")) == "market_data":
        return "market_only"
    if "social_only" in risk_types:
        return "rumor"
    if category in OFFICIAL_SOURCE_CATEGORIES and "official_confirmation" in bonuses_applied:
        return "confirmed"
    if bonuses_applied:
        return "partially_confirmed"
    return "partially_confirmed"


def calculate_confidence_score(
    event: dict[str, Any],
    announcements: list[dict[str, Any]],
    market_signal: dict[str, Any],
    market_data: dict[str, Any],
    sources_config: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[int, str, list[dict[str, str]], str, list[str]]:
    del sources_config

    score = _base_score(str(event.get("source_type", "")), rules)
    category = _source_category(str(event.get("source_type", "")), rules)
    reason_parts = [f"base:{category}=+{score}"]
    bonuses_applied: set[str] = set()

    raw_flags = event_risk_flags(
        event,
        announcements,
        {},
        as_of=f"{market_data.get('trade_date')}T23:59:59+08:00" if market_data.get("trade_date") else None,
    )
    risk_flags = _normalized_risk_flags(event, raw_flags, announcements, market_signal, rules)

    if _is_old_news(event, market_data, rules):
        _add_flag(risk_flags, "old_news", "信息发布时间超过配置阈值，需防范旧闻新炒。", "medium")

    bonuses = rules.get("bonuses", {})
    if _has_independent_sources(event):
        bonus = int(bonuses.get("independent_sources_2_or_more", 0))
        score += bonus
        bonuses_applied.add("independent_sources_2_or_more")
        reason_parts.append(f"bonus:independent_sources_2_or_more=+{bonus}")

    if _has_official_confirmation(event, announcements, rules):
        bonus = int(bonuses.get("official_confirmation", 0))
        score += bonus
        bonuses_applied.add("official_confirmation")
        reason_parts.append(f"bonus:official_confirmation=+{bonus}")

    if market_signal and _market_volume_confirmed(market_signal, rules):
        bonus = int(bonuses.get("market_volume_confirmed", 0))
        score += bonus
        bonuses_applied.add("market_volume_confirmed")
        reason_parts.append(f"bonus:market_volume_confirmed=+{bonus}")

    if market_signal and _sector_strength_confirmed(market_signal, rules):
        bonus = int(bonuses.get("sector_strength_confirmed", 0))
        score += bonus
        bonuses_applied.add("sector_strength_confirmed")
        reason_parts.append(f"bonus:sector_strength_confirmed=+{bonus}")

    if _policy_continuity(event, rules):
        bonus = int(bonuses.get("policy_continuity", 0))
        score += bonus
        bonuses_applied.add("policy_continuity")
        reason_parts.append(f"bonus:policy_continuity=+{bonus}")

    penalties = rules.get("penalties", {})
    for risk in risk_flags:
        penalty = int(penalties.get(risk["risk_type"], 0))
        if penalty:
            score -= penalty
            reason_parts.append(f"penalty:{risk['risk_type']}=-{penalty}")

    final_score = _bounded_score(score, rules)
    if final_score != score:
        reason_parts.append(f"bounded={final_score}")

    status = _verification_status(event, risk_flags, bonuses_applied, rules)
    reason_parts.append(f"status={status}")
    return final_score, status, risk_flags, "；".join(reason_parts), reason_parts


def score_events(
    events: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    market_data: dict[str, Any],
    sources_config: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []

    for event in events:
        topic = classify_topic(event)
        market_signal = _sector_market(topic, market_data)
        confidence_score, verification_status, risk_flags, reason, breakdown = calculate_confidence_score(
            event,
            announcements,
            market_signal,
            market_data,
            sources_config,
            rules,
        )
        scored.append(
            {
                **event,
                "topic": topic,
                "verification_status": verification_status,
                "fact_status": verification_status,
                "confidence_score": confidence_score,
                "risk_flags": risk_flags,
                "market_signal": market_signal,
                "reason": reason,
                "score_breakdown": breakdown,
            }
        )

    return sorted(scored, key=lambda item: (-int(item["confidence_score"]), item["topic"], item["event_id"]))

"""Generate Chinese-readable markdown and CSV reports."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from risk_filter import announcement_risk_flags
from verify_sources import source_base_confidence


BLOCKED_TERMS = ["买入", "卖出", "推荐买", "满仓", "梭哈", "必涨", "明天涨停", "稳赚"]
HIGH_CONFIDENCE_MIN_SCORE = 60
WATCH_POOL_LIMIT = 5

VERIFICATION_LABELS = {
    "confirmed": "已确认",
    "partially_confirmed": "部分确认",
    "rumor": "未证实传闻",
    "contradicted": "被否定",
    "stale": "旧闻",
    "market_only": "仅有市场反应",
}

SOURCE_TYPE_LABELS = {
    "government_policy": "政策文件",
    "policy_file": "政策文件",
    "official_announcement": "公司公告",
    "company_announcement": "公司公告",
    "exchange_announcement": "交易所公告",
    "exchange_disclosure": "交易所公告",
    "financial_news": "财经媒体",
    "mainstream_media": "财经媒体",
    "industry_news": "行业媒体",
    "industry_media": "行业媒体",
    "search_api": "搜索API",
    "overseas_news": "海外新闻",
    "social_sentiment": "社媒情绪",
    "social_media": "社媒情绪",
    "guba": "社媒情绪",
    "screenshot": "社媒情绪",
    "market_data": "行情数据",
    "fallback": "fallback",
    "unknown": "未知来源",
}

RISK_FLAG_LABELS = {
    "social_only": "仅社媒消息",
    "old_news": "旧闻新炒",
    "company_denial": "公司否认",
    "reduction_announcement": "减持风险",
    "regulatory_warning": "监管风险",
    "high_position_chasing_risk": "高位追涨风险",
    "title_body_mismatch": "标题正文不一致",
    "clarification_no_business": "公司否认",
    "shareholder_reduction": "减持风险",
    "regulatory_letter": "监管风险",
    "announcement_conflict": "公告冲突",
    "screenshot_only": "截图来源风险",
    "low_quality_source": "低可信来源",
    "unverified_rumor": "未证实传闻",
    "hype_language": "情绪化表述",
    "performance_pressure": "业绩压力",
    "stale_news": "旧闻新炒",
}

SEVERITY_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

ITEM_TYPE_LABELS = {
    "event": "热点",
    "announcement": "公告",
}

BONUS_LABELS = {
    "independent_sources_2_or_more": "两家及以上独立来源",
    "official_confirmation": "官方确认",
    "market_volume_confirmed": "成交额放量确认",
    "sector_strength_confirmed": "板块强度确认",
    "policy_continuity": "政策连续性",
}

PENALTY_LABELS = {
    "social_only": "仅社媒消息",
    "old_news": "旧闻新炒",
    "title_body_mismatch": "标题正文不一致",
    "company_denial": "公司否认",
    "reduction_announcement": "减持风险",
    "regulatory_warning": "监管风险",
    "high_position_chasing_risk": "高位追涨风险",
}


def _safe_text(value: object) -> str:
    text = str(value)
    for term in BLOCKED_TERMS:
        text = text.replace(term, "[交易化措辞已移除]")
    return text


def _format_publish_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def _verification_label(value: object) -> str:
    return VERIFICATION_LABELS.get(str(value), str(value))


def _source_type_label(value: object) -> str:
    return SOURCE_TYPE_LABELS.get(str(value), "未知来源")


def _risk_label(value: object) -> str:
    return RISK_FLAG_LABELS.get(str(value), str(value))


def _risk_labels(risk_flags: list[dict[str, str]]) -> str:
    if not risk_flags:
        return "无"
    return "；".join(_risk_label(risk["risk_type"]) for risk in risk_flags)


def _risk_text(risk_flags: list[dict[str, str]]) -> str:
    if not risk_flags:
        return "未发现显著风险点"
    return "；".join(f"{_risk_label(risk['risk_type'])}：{risk['reason']}" for risk in risk_flags)


def _market_value(market: dict[str, Any], key: str) -> str:
    value = market.get(key)
    if value is None or value == "":
        return ""
    return str(value)


def _turnover_text(market: dict[str, Any]) -> str:
    value = market.get("turnover_amount_billion")
    if value is None or value == "":
        return ""
    return f"{value}亿元"


def _market_reaction_text(event: dict[str, Any]) -> str:
    market = event.get("market_signal", {})
    if not market:
        return "暂无匹配板块行情"
    return (
        f"板块涨幅 {market.get('change_pct')}%，"
        f"涨停数量 {market.get('limit_up_count')}，"
        f"成交额 {_turnover_text(market) or '未提供'}，"
        f"放量幅度 {market.get('turnover_change_pct')}%"
    )


def _related_sector(event: dict[str, Any]) -> str:
    market = event.get("market_signal", {})
    return str(market.get("sector") or event.get("topic") or "未识别板块")


def _tomorrow_condition(event: dict[str, Any]) -> str:
    status = event.get("verification_status")
    market = event.get("market_signal", {})
    conditions = [
        "复核是否有新增官方公告、交易所披露或政策文件",
        "观察相关板块成交额变化与涨停数量是否继续匹配题材强度",
    ]
    if status == "confirmed":
        conditions.append("确认公告或政策是否出现后续进展披露")
    elif status == "market_only":
        conditions.append("等待新闻、公告或政策文件补充事实依据")
    elif status == "partially_confirmed":
        conditions.append("核对是否出现第二个独立且可追溯来源")
    if market:
        conditions.append(
            f"对照板块涨幅 {market.get('change_pct')}% 与放量幅度 {market.get('turnover_change_pct')}%"
        )
    return "；".join(conditions)


def _abandon_condition(event: dict[str, Any]) -> str:
    risk_types = {risk["risk_type"] for risk in event.get("risk_flags", [])}
    conditions = [
        "出现公司澄清否认、交易所风险提示或核心事实无法核验",
        "板块成交额明显回落且无新增高优先级来源确认",
    ]
    if "company_denial" in risk_types:
        conditions.insert(0, "公司公告已否认相关业务或相关事实")
    if "reduction_announcement" in risk_types:
        conditions.append("相关公司减持风险继续扩大")
    if "regulatory_warning" in risk_types:
        conditions.append("监管问询未获清晰回复")
    return "；".join(dict.fromkeys(conditions))


def _rumor_unverified_reason(event: dict[str, Any]) -> str:
    risk_types = {risk["risk_type"] for risk in event.get("risk_flags", [])}
    if "social_only" in risk_types:
        return "来源属于社媒、股吧或截图类情绪信号，缺少官方公告、交易所披露或政策文件确认"
    if "old_news" in risk_types:
        return "信息发布时间较旧，需要排除旧闻重复发酵"
    return "当前仅有低优先级线索，缺少可追溯的高优先级来源"


def _rumor_verification_need(event: dict[str, Any]) -> str:
    tickers = ", ".join(event.get("tickers", [])) or "相关公司"
    return f"核验 {tickers} 是否发布公告，交易所是否披露问询或澄清，政策文件是否存在原文依据"


def _announcement_summary(announcement: dict[str, Any], max_len: int = 90) -> str:
    content = " ".join(str(announcement.get("content", "")).split())
    if len(content) <= max_len:
        return content
    return content[: max_len - 1] + "..."


def _risk_treatment(risk_type: str) -> str:
    if risk_type in {"clarification_no_business", "company_denial"}:
        return "剔除"
    if risk_type in {"shareholder_reduction", "reduction_announcement", "regulatory_letter", "regulatory_warning"}:
        return "降权"
    return "仅观察"


def _stock_type(event: dict[str, Any]) -> str:
    tickers = event.get("tickers", [])
    if not tickers:
        return "板块或政策线索"
    if event.get("verification_status") == "confirmed":
        return "公告相关公司"
    if event.get("verification_status") == "market_only":
        return "板块行情线索"
    return "题材相关公司"


def _stock_role(event: dict[str, Any]) -> str:
    score = int(event.get("confidence_score", 0))
    status = event.get("verification_status")
    risk_types = {risk["risk_type"] for risk in event.get("risk_flags", [])}
    if "company_denial" in risk_types or status in {"rumor", "contradicted"}:
        return "仅观察"
    if score >= 85:
        return "龙头/中军候选"
    if score >= 60:
        return "中军候选"
    if status == "market_only":
        return "后排情绪线索"
    return "补涨/后排待复核"


def _score_reason_text(event: dict[str, Any]) -> str:
    translated: list[str] = []
    for part in event.get("score_breakdown", []):
        if part.startswith("base:"):
            key, value = part.removeprefix("base:").split("=", 1)
            translated.append(f"基础分（{_source_type_label(key)}）{value}")
        elif part.startswith("bonus:"):
            key, value = part.removeprefix("bonus:").split("=", 1)
            translated.append(f"加分（{BONUS_LABELS.get(key, key)}）{value}")
        elif part.startswith("penalty:"):
            key, value = part.removeprefix("penalty:").split("=", 1)
            translated.append(f"扣分（{PENALTY_LABELS.get(key, _risk_label(key))}）{value}")
        elif part.startswith("bounded="):
            translated.append(f"分数边界调整为 {part.split('=', 1)[1]}")
        elif part.startswith("status="):
            translated.append(f"验证状态为{_verification_label(part.split('=', 1)[1])}")
    return "；".join(translated) if translated else _safe_text(event.get("reason", ""))


def _high_confidence_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excluded = {"rumor", "contradicted", "stale"}
    return [
        event
        for event in events
        if int(event.get("confidence_score", 0)) >= HIGH_CONFIDENCE_MIN_SCORE
        and event.get("verification_status") not in excluded
    ]


def _watch_pool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excluded = {"rumor", "contradicted", "stale"}
    candidates = [
        event
        for event in events
        if event.get("verification_status") not in excluded and int(event.get("confidence_score", 0)) > 0
    ]
    return candidates[:WATCH_POOL_LIMIT]


def _write_watchlist(path: Path, events: list[dict[str, Any]]) -> None:
    fields = [
        "事件编号",
        "题材",
        "热点标题",
        "验证状态",
        "来源",
        "来源类型",
        "发布时间",
        "原始链接",
        "可信度分数",
        "风险标签",
        "评分原因",
        "重复次数",
        "对应板块",
        "板块涨幅",
        "涨停数量",
        "成交额",
        "放量幅度",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for event in events:
            market = event.get("market_signal", {})
            writer.writerow(
                {
                    "事件编号": event["event_id"],
                    "题材": _safe_text(event["topic"]),
                    "热点标题": _safe_text(event["title"]),
                    "验证状态": _verification_label(event["verification_status"]),
                    "来源": _safe_text(event["source"]),
                    "来源类型": _source_type_label(event["source_type"]),
                    "发布时间": _format_publish_time(event["publish_time"]),
                    "原始链接": event["url"],
                    "可信度分数": event["confidence_score"],
                    "风险标签": _risk_labels(event["risk_flags"]),
                    "评分原因": _score_reason_text(event),
                    "重复次数": event.get("duplicate_count", 1),
                    "对应板块": _safe_text(market.get("sector", "")),
                    "板块涨幅": _market_value(market, "change_pct"),
                    "涨停数量": _market_value(market, "limit_up_count"),
                    "成交额": _turnover_text(market),
                    "放量幅度": _market_value(market, "turnover_change_pct"),
                }
            )


def _write_risk_flags(
    path: Path,
    events: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    sources_config: dict[str, Any],
) -> None:
    fields = [
        "编号",
        "类型",
        "风险类型",
        "严重程度",
        "原因",
        "来源",
        "来源类型",
        "发布时间",
        "原始链接",
        "可信度分数",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for event in events:
            for risk in event["risk_flags"]:
                writer.writerow(
                    {
                        "编号": event["event_id"],
                        "类型": ITEM_TYPE_LABELS["event"],
                        "风险类型": _risk_label(risk["risk_type"]),
                        "严重程度": SEVERITY_LABELS.get(risk["severity"], risk["severity"]),
                        "原因": _safe_text(risk["reason"]),
                        "来源": _safe_text(event["source"]),
                        "来源类型": _source_type_label(event["source_type"]),
                        "发布时间": _format_publish_time(event["publish_time"]),
                        "原始链接": event["url"],
                        "可信度分数": event["confidence_score"],
                    }
                )

        for announcement in announcements:
            for risk in announcement_risk_flags(announcement):
                writer.writerow(
                    {
                        "编号": announcement["announcement_id"],
                        "类型": ITEM_TYPE_LABELS["announcement"],
                        "风险类型": _risk_label(risk["risk_type"]),
                        "严重程度": SEVERITY_LABELS.get(risk["severity"], risk["severity"]),
                        "原因": _safe_text(risk["reason"]),
                        "来源": _safe_text(announcement["source"]),
                        "来源类型": _source_type_label(announcement["source_type"]),
                        "发布时间": _format_publish_time(announcement["publish_time"]),
                        "原始链接": announcement["url"],
                        "可信度分数": source_base_confidence(announcement["source_type"], sources_config),
                    }
                )


def _append_high_confidence_section(lines: list[str], events: list[dict[str, Any]]) -> None:
    lines.extend(["## 一、高可信热点", ""])
    high_confidence = _high_confidence_events(events)
    if not high_confidence:
        lines.extend(["- 今日暂无达到高可信阈值的热点。", ""])
        return

    for event in high_confidence:
        lines.extend(
            [
                f"### {_safe_text(event['title'])}",
                "",
                f"- 题材：{_safe_text(event.get('topic', '未识别题材'))}",
                f"- 热点标题：{_safe_text(event['title'])}",
                f"- 可信度分数：{event['confidence_score']}/100",
                (
                    f"- 来源：{_safe_text(event['source'])}"
                    f"（{_source_type_label(event['source_type'])}，{_format_publish_time(event['publish_time'])}）"
                ),
                f"- 验证状态：{_verification_label(event['verification_status'])}",
                f"- 相关板块：{_safe_text(_related_sector(event))}",
                f"- 市场反应：{_safe_text(_market_reaction_text(event))}",
                f"- 风险点：{_safe_text(_risk_text(event.get('risk_flags', [])))}",
                f"- 明日观察条件：{_safe_text(_tomorrow_condition(event))}",
                f"- 放弃条件：{_safe_text(_abandon_condition(event))}",
                "",
            ]
        )


def _append_rumor_section(lines: list[str], events: list[dict[str, Any]]) -> None:
    lines.extend(["## 二、未证实传闻", ""])
    rumors = [event for event in events if event.get("verification_status") == "rumor"]
    if not rumors:
        lines.extend(["- 今日暂无未证实传闻。", ""])
        return

    for event in rumors:
        lines.extend(
            [
                f"### {_safe_text(event['title'])}",
                "",
                f"- 标题：{_safe_text(event['title'])}",
                (
                    f"- 传闻来源：{_safe_text(event['source'])}"
                    f"（{_source_type_label(event['source_type'])}，{_format_publish_time(event['publish_time'])}）"
                ),
                f"- 为什么未证实：{_safe_text(_rumor_unverified_reason(event))}",
                f"- 需要验证什么：{_safe_text(_rumor_verification_need(event))}",
                "- 是否进入观察池：否",
                "",
            ]
        )


def _append_risk_announcement_section(lines: list[str], announcements: list[dict[str, Any]]) -> None:
    lines.extend(["## 三、风险公告", ""])
    rows: list[tuple[dict[str, Any], dict[str, str]]] = []
    for announcement in announcements:
        for risk in announcement_risk_flags(announcement):
            rows.append((announcement, risk))

    if not rows:
        lines.extend(["- 今日暂无风险公告。", ""])
        return

    for announcement, risk in rows:
        risk_type = risk["risk_type"]
        lines.extend(
            [
                f"### {_safe_text(announcement['company'])}（{announcement['ticker']}）",
                "",
                f"- 股票/公司：{announcement['ticker']} / {_safe_text(announcement['company'])}",
                f"- 风险类型：{_risk_label(risk_type)}",
                f"- 公告摘要：{_safe_text(_announcement_summary(announcement))}",
                f"- 处理建议：{_risk_treatment(risk_type)}",
                "",
            ]
        )


def _append_watch_pool_section(lines: list[str], events: list[dict[str, Any]]) -> None:
    lines.extend(["## 四、明日观察池", ""])
    watch_pool = _watch_pool_events(events)
    if not watch_pool:
        lines.extend(["- 暂无可进入明日观察池的线索。", ""])
        return

    for event in watch_pool:
        lines.extend(
            [
                f"### {_safe_text(event.get('topic', '未识别题材'))}",
                "",
                f"- 题材：{_safe_text(event.get('topic', '未识别题材'))}",
                f"- 相关股票类型：{_safe_text(_stock_type(event))}",
                f"- 龙头/中军/补涨/后排：{_safe_text(_stock_role(event))}",
                f"- 观察条件：{_safe_text(_tomorrow_condition(event))}",
                f"- 风险：{_safe_text(_risk_text(event.get('risk_flags', [])))}",
                f"- 放弃条件：{_safe_text(_abandon_condition(event))}",
                "",
            ]
        )


def _coverage_list(values: object, empty_text: str = "无") -> str:
    if not values:
        return empty_text
    if isinstance(values, list):
        return "、".join(str(value) for value in values) if values else empty_text
    return str(values)


def _coverage_source_type_text(values: object) -> str:
    if not isinstance(values, dict) or not values:
        return "暂无"
    parts = []
    for source_type, count in values.items():
        parts.append(f"{_source_type_label(source_type)} {count} 条")
    return "、".join(parts)


def _coverage_source_status_lines(values: object) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["- 数据源状态：暂无"]
    lines = ["- 数据源状态："]
    for item in values:
        if not isinstance(item, dict):
            continue
        source_name = _safe_text(item.get("source_name") or item.get("source") or "未命名数据源")
        source_type = _source_type_label(item.get("source_type"))
        enabled_text = "启用" if item.get("enabled", True) else "停用"
        priority_text = str(item.get("priority", ""))
        status = _coverage_status(item)
        status_text = {
            "success": "成功",
            "failed": "失败",
            "timeout": "超时",
            "skipped": "跳过",
            "placeholder": "占位源",
            "fallback": "fallback",
        }.get(status, status or "未知")
        reason = _short_text(_safe_text(item.get("reason") or item.get("warning") or ""), 140)
        lines.append(
            f"  - {source_name}｜{source_type}｜{enabled_text}｜优先级 {priority_text}｜{status_text}｜{reason}"
        )
    return lines


def _coverage_status(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    reason = str(item.get("reason") or item.get("warning") or "")
    if status in {"success", "failed", "timeout", "skipped", "placeholder", "fallback"}:
        return status
    if "占位源" in reason or "未接入真实接口" in reason:
        return "placeholder"
    if "未启用" in reason or "未配置" in reason or "URL 为空" in reason:
        return "skipped"
    if item.get("success"):
        return "success"
    return "failed"


def _coverage_sources_by_status(values: object, target_status: str) -> list[str]:
    if not isinstance(values, list):
        return []
    names = []
    for item in values:
        if isinstance(item, dict) and _coverage_status(item) == target_status:
            names.append(_safe_text(item.get("source_name") or item.get("source") or "未命名数据源"))
    return names


def _coverage_enabled_success_rate(values: object) -> str:
    if not isinstance(values, list):
        return "暂无启用数据源"
    active = [item for item in values if isinstance(item, dict) and _coverage_status(item) in {"success", "failed", "timeout"}]
    if not active:
        return "暂无启用数据源"
    success_count = sum(1 for item in active if _coverage_status(item) == "success")
    return f"{success_count / len(active) * 100:.0f}%"


def _coverage_tavily_count(values: object) -> int:
    if not isinstance(values, list):
        return 0
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("source_name") or item.get("source") or "")
        if "Tavily" in name:
            return int(item.get("item_count", 0) or item.get("count", 0) or 0)
    return 0


def _short_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _append_coverage_section(lines: list[str], market_data: dict[str, Any]) -> None:
    coverage = market_data.get("coverage_report")
    if not isinstance(coverage, dict) or not coverage:
        return

    official_checked = _coverage_list(coverage.get("official_sources_checked"), "未配置或未返回官方源核验结果")
    warnings = _coverage_list(coverage.get("warnings"), "无")
    source_status = coverage.get("source_status")
    skipped_sources = _coverage_list(_coverage_sources_by_status(source_status, "skipped"), "无")
    placeholder_sources = _coverage_list(_coverage_sources_by_status(source_status, "placeholder"), "无")
    timeout_sources = _coverage_list(_coverage_sources_by_status(source_status, "timeout"), "无")
    fallback_usage = _coverage_list(coverage.get("fallback_usage"), "未使用 fallback")
    lines.extend(
        [
            "## 数据覆盖范围",
            "",
            "> 本报告不代表全网穷尽搜索，仅代表已配置数据源范围内的自动检索和交叉验证结果。",
            "> 未启用源、占位源、RSS URL 为空的数据源不计入启用源成功率。",
            "",
            f"- 启用源成功率：{_coverage_enabled_success_rate(source_status)}",
            f"- Tavily 返回结果：{_coverage_tavily_count(source_status)}",
            f"- 去重后结果：{coverage.get('deduped_results_count', 0)}",
            f"- 高质量新闻：{coverage.get('high_quality_news_count', 0)}",
            f"- 使用 fallback：{fallback_usage}",
            f"- 跳过源：{skipped_sources}",
            f"- 占位源：{placeholder_sources}",
            f"- 超时源：{timeout_sources}",
            f"- 独立域名数量：{coverage.get('unique_domains_count', 0)}",
            f"- 原始候选结果数量：{coverage.get('raw_results_count', 0)}",
            f"- 来源类型覆盖：{_coverage_source_type_text(coverage.get('source_type_coverage'))}",
            f"- 官方源核验情况：{official_checked}",
            f"- 重要警告：{warnings}",
        ]
    )
    lines.extend(_coverage_source_status_lines(source_status))
    lines.append("")


def _write_markdown(
    path: Path,
    events: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    market_data: dict[str, Any],
) -> None:
    report_date = market_data.get("trade_date", "模拟日期")
    lines = [
        f"# A股热点情报日报｜{report_date}",
        "",
        "> 本报告仅用于人工复核，不构成交易指令，也不能用于自动交易。",
        "",
    ]
    if market_data.get("empty_message"):
        lines.extend([str(market_data["empty_message"]), ""])

    _append_coverage_section(lines, market_data)
    _append_high_confidence_section(lines, events)
    _append_rumor_section(lines, events)
    _append_risk_announcement_section(lines, announcements)
    _append_watch_pool_section(lines, events)

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def generate_outputs(
    events: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    market_data: dict[str, Any],
    sources_config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "daily_report": output_dir / "daily_report.md",
        "watchlist": output_dir / "watchlist.csv",
        "risk_flags": output_dir / "risk_flags.csv",
    }
    _write_markdown(files["daily_report"], events, announcements, market_data)
    _write_watchlist(files["watchlist"], events)
    _write_risk_flags(files["risk_flags"], events, announcements, sources_config)
    return files

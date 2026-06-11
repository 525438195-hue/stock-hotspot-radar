"""Aggregate online fetchers and manual fallbacks."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from manual_input import load_manual_announcements, load_manual_market, load_manual_news
from query_builder import QueryBuilder
from source_config import configured_sources, status_from_record

from .announcement_fetcher import AnnouncementFetcher
from .market_fetcher import MarketFetcher
from .search_fetcher import SearchFetcher
from .social_fetcher import SocialSentimentFetcher


DEFAULT_SEARCH_KEYWORDS = ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "消费电子", "新能源", "医药", "证券"]
AUTO_MAX_SECONDS = 90


def fetch_all(
    sources_config: dict[str, Any],
    data_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[str]]:
    deadline = time.monotonic() + AUTO_MAX_SECONDS
    source_status: list[dict[str, Any]] = []
    warnings: list[str] = []
    fallback_usage: list[str] = []

    events, search_coverage = _fetch_search_events(sources_config, data_dir, source_status, warnings, deadline)
    social_events = _fetch_social_events(sources_config, data_dir, source_status, deadline, fallback_usage)
    has_online_candidates = int(search_coverage.get("raw_results_count", 0) or 0) > 0
    if not events and not has_online_candidates:
        _log("正在读取：manual fallback - manual_news.csv")
        manual_events = load_manual_news(data_dir / "manual_news.csv")
        if manual_events:
            _log(f"成功：获取 {len(manual_events)} 条")
            warnings.append("联网新闻与搜索候选为空，已回退到 manual_news.csv")
            fallback_usage.append("manual_news.csv")
        else:
            _log("失败：原因 manual_news.csv 没有有效数据，已跳过")
            warnings.append("联网新闻与搜索候选为空，manual_news.csv 也没有有效数据")
        events = manual_events
    elif not events and has_online_candidates:
        warnings.append("联网搜索已有候选，但均未达到 A股相关性保留阈值，未回退到 manual_news.csv")
    events.extend(social_events)

    announcements = _fetch_announcements(sources_config, source_status, deadline)
    if not announcements:
        _log("正在读取：manual fallback - manual_announcements.csv")
        manual_announcements = load_manual_announcements(data_dir / "manual_announcements.csv")
        if manual_announcements:
            _log(f"成功：获取 {len(manual_announcements)} 条")
            warnings.append("公告源读取失败或为空，已回退到 manual_announcements.csv")
            fallback_usage.append("manual_announcements.csv")
        else:
            _log("失败：原因 manual_announcements.csv 没有有效数据，已跳过")
            warnings.append("公告源读取失败或为空，manual_announcements.csv 也没有有效数据")
        announcements = manual_announcements

    market_result = _fetch_market_data(sources_config, source_status, deadline)
    market_data = (
        market_result["items"]
        if market_result["success"] and isinstance(market_result["items"], dict)
        else {"trade_date": "", "sectors": []}
    )
    if not market_data.get("sectors"):
        _log("正在读取：manual fallback - manual_market.csv")
        manual_market = load_manual_market(data_dir / "manual_market.csv")
        if manual_market.get("sectors"):
            if market_result.get("status") == "timeout":
                _log("AKShare 超时，已使用 manual_market fallback。")
            _log(f"成功：获取 {len(manual_market.get('sectors', []))} 条")
            warnings.append("行情源读取失败或为空，已回退到 manual_market.csv")
            fallback_usage.append("manual_market.csv")
        else:
            _log("失败：原因 manual_market.csv 没有有效数据，已跳过")
            warnings.append("行情源读取失败或为空，manual_market.csv 也没有有效数据")
        market_data = manual_market

    if search_coverage:
        search_coverage["official_sources_checked"] = _official_sources_checked(sources_config)
        search_coverage["source_status"] = source_status
        search_coverage["fallback_usage"] = fallback_usage
        market_data["coverage_report"] = search_coverage

    if not events and not announcements and not market_data.get("sectors"):
        market_data["empty_message"] = "暂无自动联网数据，且暂无手动导入数据"

    source_status.sort(key=lambda item: (int(item.get("priority", 99)), str(item.get("source_name") or item.get("source") or "")))
    if search_coverage:
        market_data["coverage_report"]["source_status"] = source_status
        market_data["coverage_report"]["successful_sources"] = [
            str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "success"
        ]
        market_data["coverage_report"]["failed_sources"] = [
            str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "failed"
        ]
        market_data["coverage_report"]["skipped_sources"] = [
            str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "skipped"
        ]
        market_data["coverage_report"]["timeout_sources"] = [
            str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "timeout"
        ]
        market_data["coverage_report"]["fallback_usage"] = fallback_usage

    return events, announcements, market_data, source_status, warnings


def _fetch_search_events(
    sources_config: dict[str, Any],
    data_dir: Path,
    source_status: list[dict[str, Any]],
    warnings: list[str],
    deadline: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if _deadline_reached(deadline):
        warnings.append("auto 流程已超过 90 秒，停止联网搜索")
        return [], {}
    search_keywords = sources_config.get("search", {}).get("base_keywords") or DEFAULT_SEARCH_KEYWORDS
    queries = QueryBuilder([str(keyword) for keyword in search_keywords]).build()
    result = SearchFetcher(queries, sources_config, data_dir.parent, deadline=deadline).safe_fetch()

    for status in result.get("source_status", []):
        source_status.append(_search_status_row(status))
    if result.get("warning"):
        warnings.append(str(result["warning"]))

    events = result["items"] if result["success"] and isinstance(result["items"], list) else []
    coverage = result.get("coverage", {})
    return events, coverage if isinstance(coverage, dict) else {}


def _fetch_social_events(
    sources_config: dict[str, Any],
    data_dir: Path,
    source_status: list[dict[str, Any]],
    deadline: float,
    fallback_usage: list[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in configured_sources(sources_config, "social_sources"):
        _log(f"正在读取：{record.get('source_name', '社媒情绪源')}")
        if _deadline_reached(deadline):
            source_status.append(status_from_record(record, status="timeout", reason="超时：auto 流程已超过 90 秒，已切换 fallback", item_count=0))
            _log("超时：已切换 fallback")
            continue
        if record["status"] == "skipped":
            source_status.append(status_from_record(record, item_count=0))
            _log(f"失败：原因 {record.get('reason', '已跳过')}，已跳过")
            continue
        result = SocialSentimentFetcher([record], data_dir.parent).safe_fetch()
        source_status.append(_status_row(result, record))
        items = result["items"] if isinstance(result.get("items"), list) else []
        if result["success"] and items:
            events.extend(items)
            _log(f"成功：获取 {len(items)} 条")
            if record.get("key") == "manual_rumors":
                fallback_usage.append("manual_rumors.csv")
        else:
            _log(f"失败：原因 {result.get('warning', '数据源返回为空')}，已跳过")
    return events


def _fetch_announcements(
    sources_config: dict[str, Any],
    source_status: list[dict[str, Any]],
    deadline: float,
) -> list[dict[str, Any]]:
    announcements: list[dict[str, Any]] = []
    for record in configured_sources(sources_config, "official_sources"):
        _log(f"正在抓取：{record.get('source_name', '公告源')}")
        if _deadline_reached(deadline):
            source_status.append(status_from_record(record, status="timeout", reason="超时：auto 流程已超过 90 秒，已切换 fallback", item_count=0))
            _log("超时：已切换 fallback")
            continue
        if record["status"] == "skipped":
            source_status.append(status_from_record(record, item_count=0))
            _log(f"失败：原因 {record.get('reason', '已跳过')}，已跳过")
            continue
        result = AnnouncementFetcher([record]).safe_fetch()
        source_status.append(_status_row(result, record))
        if result["success"] and isinstance(result["items"], list):
            announcements.extend(result["items"])
            _log(f"成功：获取 {len(result['items'])} 条")
        else:
            _log(f"失败：原因 {result.get('warning', '数据源返回为空')}，已跳过")
    return announcements


def _fetch_market_data(sources_config: dict[str, Any], source_status: list[dict[str, Any]], deadline: float) -> dict[str, Any]:
    market_records = configured_sources(sources_config, "market_sources")
    if not market_records:
        return {
            "success": False,
            "source": "行情源",
            "items": {"trade_date": "", "sectors": []},
            "warning": "未配置行情源",
        }
    record = market_records[0]
    _log("正在抓取：AKShare 行情")
    if _deadline_reached(deadline):
        source_status.append(status_from_record(record, status="timeout", reason="超时：auto 流程已超过 90 秒，已切换 fallback", item_count=0))
        _log("超时：已切换 fallback")
        return {"success": False, "status": "timeout", "source": record["source_name"], "items": {"trade_date": "", "sectors": []}, "warning": "超时：auto 流程已超过 90 秒，已切换 fallback"}
    if record["status"] == "skipped":
        source_status.append(status_from_record(record, item_count=0))
        _log(f"失败：原因 {record.get('reason', '已跳过')}，已跳过")
        return {"success": False, "status": "skipped", "source": record["source_name"], "items": {"trade_date": "", "sectors": []}, "warning": record["reason"]}
    result = MarketFetcher().safe_fetch()
    source_status.append(_status_row(result, record))
    if result.get("status") == "success":
        items = result.get("items", {})
        count = len(items.get("sectors", [])) if isinstance(items, dict) else 0
        _log(f"成功：获取 {count} 条")
    elif result.get("status") == "timeout":
        _log("超时：已切换 fallback")
    else:
        _log(f"失败：原因 {result.get('warning', '数据源返回为空')}，已跳过")
    return result


def _official_sources_checked(sources_config: dict[str, Any]) -> list[str]:
    official_sources = configured_sources(sources_config, "official_sources")
    checked = []
    for source in official_sources:
        name = str(source.get("source_name", "官方源"))
        if source.get("status") == "skipped":
            checked.append(f"{name}（{source.get('reason', '已跳过')}）")
        else:
            checked.append(name)
    return checked


def _status_row(result: dict[str, Any], record: dict[str, Any] | None = None) -> dict[str, Any]:
    items = result.get("items", [])
    count = len(items.get("sectors", [])) if isinstance(items, dict) else len(items)
    if result.get("status"):
        status = str(result["status"])
    elif result.get("success") and count:
        status = "success"
    elif result.get("success"):
        status = "skipped"
    else:
        status = "failed"
    reason = str(result.get("warning", "")) or ("读取成功" if status == "success" else "")
    if record:
        return status_from_record(record, status=status, reason=reason, item_count=count)
    row = {
        "source_name": result.get("source", ""),
        "source_type": "unknown",
        "enabled": True,
        "priority": 99,
        "status": status,
        "reason": reason,
        "source": result.get("source", ""),
        "success": status == "success",
        "item_count": count,
        "warning": reason,
    }
    return row


def _search_status_row(status: dict[str, Any]) -> dict[str, Any]:
    state = str(status.get("status", ""))
    return {
        "source_name": status.get("source_name", status.get("source", "")),
        "source_type": status.get("source_type", "unknown"),
        "enabled": bool(status.get("enabled", True)),
        "priority": int(status.get("priority", 99)),
        "success": state == "success",
        "status": state,
        "item_count": int(status.get("item_count", 0) or 0),
        "reason": status.get("reason", status.get("warning", "")),
        "source": status.get("source", status.get("source_name", "")),
        "warning": status.get("warning", status.get("reason", "")),
    }


def _deadline_reached(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _log(message: str) -> None:
    print(message, flush=True)

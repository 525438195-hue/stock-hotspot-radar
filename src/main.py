"""Run the minimal A-share hotspot intelligence pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from dedupe import dedupe_events
from fetchers.fetch_all import fetch_all
from generate_report import generate_outputs
from manual_input import load_manual_data
from risk_filter import announcement_risk_flags
from score_events import score_events
from source_config import load_source_config
from stock_candidate_builder import build_stock_candidates


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
    except Exception:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return loaded


def load_sample_data(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events = load_json(data_dir / "sample_events.json")
    announcements = load_json(data_dir / "sample_announcements.json")
    market_data = load_json(data_dir / "sample_market.json")
    return events, announcements, market_data


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A股热点情报筛选系统")
    parser.add_argument(
        "--mode",
        choices=["sample", "manual", "auto"],
        default="sample",
        help="sample 读取模拟 JSON；manual 读取手动 CSV；auto 自动联网读取并失败回退到手动 CSV。",
    )
    return parser.parse_args(argv)


def run_pipeline(mode: str = "sample") -> dict[str, Path]:
    total_start = time.perf_counter()
    refresh_timing: dict[str, float] = {
        "total_seconds": 0.0,
        "query_build_seconds": 0.0,
        "tavily_fetch_seconds": 0.0,
        "filter_seconds": 0.0,
        "candidate_build_seconds": 0.0,
        "news_summary_seconds": 0.0,
        "write_output_seconds": 0.0,
    }
    ensure_runtime_dirs(PROJECT_ROOT)
    sources_config = load_source_config(PROJECT_ROOT)
    scoring_rules = load_config(PROJECT_ROOT / "config" / "scoring_rules.yaml")

    data_dir = PROJECT_ROOT / "data"
    source_status: list[dict[str, Any]] = []
    warnings: list[str] = []
    if mode == "manual":
        events, announcements, market_data = load_manual_data(data_dir)
        source_status = [
            {"source": "manual_news.csv", "success": True, "item_count": len(events), "warning": ""},
            {"source": "manual_announcements.csv", "success": True, "item_count": len(announcements), "warning": ""},
            {
                "source": "manual_market.csv",
                "success": True,
                "item_count": len(market_data.get("sectors", [])),
                "warning": "",
            },
        ]
    elif mode == "auto":
        events, announcements, market_data, source_status, warnings = fetch_all(sources_config, data_dir)
    else:
        events, announcements, market_data = load_sample_data(data_dir)
        source_status = [
            {"source": "sample_events.json", "success": True, "item_count": len(events), "warning": ""},
            {"source": "sample_announcements.json", "success": True, "item_count": len(announcements), "warning": ""},
            {
                "source": "sample_market.json",
                "success": True,
                "item_count": len(market_data.get("sectors", [])),
                "warning": "",
            },
        ]

    coverage_timing = {}
    if isinstance(market_data.get("coverage_report"), dict):
        coverage_timing = dict(market_data.get("coverage_report", {}).get("refresh_timing", {}) or {})
    for key in ("query_build_seconds", "tavily_fetch_seconds", "filter_seconds"):
        refresh_timing[key] = float(coverage_timing.get(key, 0.0) or 0.0)

    deduped_events = dedupe_events(events)
    scored_events = score_events(deduped_events, announcements, market_data, sources_config, scoring_rules)
    write_start = time.perf_counter()
    files = generate_outputs(
        scored_events,
        announcements,
        market_data,
        sources_config,
        PROJECT_ROOT / "outputs",
    )
    refresh_timing["write_output_seconds"] = round(time.perf_counter() - write_start, 3)

    risk_count = sum(len(event["risk_flags"]) for event in scored_events)
    risk_announcement_count = sum(1 for announcement in announcements if announcement_risk_flags(announcement))
    high_confidence_count = sum(
        1
        for event in scored_events
        if int(event.get("confidence_score", 0)) >= 60
        and event.get("verification_status") not in {"rumor", "contradicted", "stale"}
    )
    candidate_start = time.perf_counter()
    files["stock_candidates"] = build_stock_candidates(PROJECT_ROOT / "outputs", data_dir, market_data)
    refresh_timing["candidate_build_seconds"] = round(time.perf_counter() - candidate_start, 3)
    summary_start = time.perf_counter()
    files["news_summary"] = write_news_summary(PROJECT_ROOT / "outputs")
    refresh_timing["news_summary_seconds"] = round(time.perf_counter() - summary_start, 3)
    stock_candidate_count = _csv_row_count(PROJECT_ROOT / "outputs" / "stock_candidates.csv")

    state_file = PROJECT_ROOT / "outputs" / "report_state.json"
    files["report_state"] = state_file
    refresh_timing["total_seconds"] = round(time.perf_counter() - total_start, 3)
    save_report_state(
        state_file,
        mode=mode,
        raw_count=len(events),
        deduped_count=len(deduped_events),
        risk_count=risk_count,
        high_confidence_count=high_confidence_count,
        risk_announcement_count=risk_announcement_count,
        source_status=source_status,
        warnings=warnings,
        coverage_report=market_data.get("coverage_report", {}),
        stock_candidate_count=stock_candidate_count,
        refresh_timing=refresh_timing,
    )

    print(f"A股热点情报筛选系统已完成 {mode} 模式流程。")
    if market_data.get("empty_message"):
        print(market_data["empty_message"])
    for warning in warnings:
        print(f"告警：{warning}")
    print(f"原始热点：{len(events)} 条")
    print(f"去重后热点：{len(deduped_events)} 条")
    print(f"风险标记：{risk_count} 条")
    print("输出文件：")
    for path in files.values():
        print(f"- {display_path(path)}")
    return files


def display_path(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def ensure_runtime_dirs(project_root: Path) -> None:
    (project_root / "outputs").mkdir(parents=True, exist_ok=True)
    (project_root / "data" / "cache").mkdir(parents=True, exist_ok=True)


def write_news_summary(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / "search_results_deduped.csv"
    target_path = output_dir / "news_summary.csv"
    fields = ["题材", "今日催化", "核心新闻", "来源", "原始链接", "市场反应", "风险点", "总结等级"]
    rows: list[dict[str, str]] = []
    if source_path.exists():
        with source_path.open(encoding="utf-8-sig", newline="") as file:
            grouped: dict[str, list[dict[str, str]]] = {}
            for row in csv.DictReader(file):
                if row.get("是否保留") != "是":
                    continue
                topic = row.get("题材") or _topic_from_query(row.get("查询词", "")) or "未识别题材"
                grouped.setdefault(topic, []).append(row)
            for topic, items in grouped.items():
                for row in items[:3]:
                    result_type = row.get("结果类型", "")
                    rows.append(
                        {
                            "题材": topic,
                            "今日催化": row.get("摘要", "")[:120],
                            "核心新闻": row.get("标题", ""),
                            "来源": row.get("来源", ""),
                            "原始链接": row.get("原始链接", ""),
                            "市场反应": "等待行情确认" if result_type == "题材参考" else "关注板块强度和资金变化",
                            "风险点": row.get("过滤原因", ""),
                            "总结等级": result_type or "题材参考",
                        }
                    )
    with target_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target_path


def _topic_from_query(query: str) -> str:
    for topic in ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "新能源", "消费电子", "医药", "证券"]:
        if topic in str(query):
            return topic
    return str(query).split()[0] if query else ""


def save_report_state(path: Path, **state: Any) -> None:
    source_status = list(state.get("source_status", []))
    coverage_report = dict(state.get("coverage_report", {}) or {})
    source_status_table = _source_status_table(source_status)
    enabled_source_success_rate = _enabled_source_success_rate(source_status_table)
    source_success_rate = enabled_source_success_rate if enabled_source_success_rate is not None else 0.0
    fallback_used = [row["source_name"] for row in source_status_table if row["status"] == "fallback"]
    tavily_result_count = _tavily_result_count(source_status_table)
    deduped_result_count = int(coverage_report.get("deduped_results_count", 0) or coverage_report.get("deduped_result_count", 0) or 0)
    high_quality_news_count = int(coverage_report.get("high_quality_news_count", 0) or 0)
    refresh_timing = _refresh_timing(state.get("refresh_timing", {}))
    payload = {
        "last_update_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": state.get("mode", ""),
        "raw_count": int(state.get("raw_count", 0)),
        "deduped_count": int(state.get("deduped_count", 0)),
        "high_confidence_count": int(state.get("high_confidence_count", 0)),
        "risk_announcement_count": int(state.get("risk_announcement_count", 0)),
        "risk_count": int(state.get("risk_count", 0)),
        "source_success_rate": source_success_rate,
        "enabled_source_success_rate": enabled_source_success_rate,
        "source_status": source_status,
        "source_status_table": source_status_table,
        "tavily_result_count": tavily_result_count,
        "deduped_result_count": deduped_result_count,
        "high_quality_news_count": high_quality_news_count,
        "stock_candidate_count": int(state.get("stock_candidate_count", 0)),
        "refresh_timing": refresh_timing,
        "fallback_used": fallback_used,
        "successful_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "success"],
        "failed_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "failed"],
        "timeout_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "timeout"],
        "fallback_usage": fallback_used or list(coverage_report.get("fallback_usage", [])),
        "warnings": list(state.get("warnings", [])),
        "coverage_report": coverage_report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_status_table(source_status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in source_status:
        status = _normalized_source_status(item)
        source_name = str(item.get("source_name") or item.get("source") or "")
        source_type = str(item.get("source_type") or "unknown")
        key = (source_name, status)
        if key in seen:
            continue
        seen.add(key)
        counted = status in {"success", "failed", "timeout"}
        reason = str(item.get("reason") or item.get("warning") or "")
        rows.append(
            {
                "source_name": source_name,
                "source_type": source_type,
                "status": status,
                "count": int(item.get("item_count", 0) or 0),
                "counted_in_success_rate": counted,
                "reason": reason or "无",
                "note": _source_note(item, status),
            }
        )
    return rows


def _refresh_timing(value: object) -> dict[str, float]:
    keys = [
        "total_seconds",
        "query_build_seconds",
        "tavily_fetch_seconds",
        "filter_seconds",
        "candidate_build_seconds",
        "news_summary_seconds",
        "write_output_seconds",
    ]
    timing = value if isinstance(value, dict) else {}
    return {key: round(float(timing.get(key, 0.0) or 0.0), 3) for key in keys}


def _normalized_source_status(item: dict[str, Any]) -> str:
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


def _source_note(item: dict[str, Any], status: str) -> str:
    source_name = str(item.get("source_name") or item.get("source") or "")
    if status == "placeholder":
        return "未接入真实接口"
    if status == "skipped":
        return "未启用或配置不完整，不计入成功率"
    if status == "fallback":
        return str(item.get("note") or "兜底数据")
    if "Tavily" in source_name:
        return "主搜索源"
    if status == "timeout":
        return "超时，若有 fallback 会单独显示"
    if status == "failed":
        return "启用源请求失败，计入成功率"
    return "启用源成功返回有效数据"


def _enabled_source_success_rate(rows: list[dict[str, Any]]) -> float | None:
    active = [row for row in rows if row.get("counted_in_success_rate")]
    if not active:
        return None
    success_count = sum(1 for row in active if row.get("status") == "success")
    return round(success_count / len(active), 4)


def _tavily_result_count(rows: list[dict[str, Any]]) -> int:
    for row in rows:
        if "Tavily" in str(row.get("source_name", "")):
            return int(row.get("count", 0) or 0)
    return 0


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as file:
        return sum(1 for _ in csv.DictReader(file))


def main(argv: list[str] | None = None) -> dict[str, Path]:
    args = parse_args(argv or [])
    return run_pipeline(args.mode)


if __name__ == "__main__":
    main(sys.argv[1:])

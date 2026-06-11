"""Run the minimal A-share hotspot intelligence pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
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

    deduped_events = dedupe_events(events)
    scored_events = score_events(deduped_events, announcements, market_data, sources_config, scoring_rules)
    files = generate_outputs(
        scored_events,
        announcements,
        market_data,
        sources_config,
        PROJECT_ROOT / "outputs",
    )

    risk_count = sum(len(event["risk_flags"]) for event in scored_events)
    risk_announcement_count = sum(1 for announcement in announcements if announcement_risk_flags(announcement))
    high_confidence_count = sum(
        1
        for event in scored_events
        if int(event.get("confidence_score", 0)) >= 60
        and event.get("verification_status") not in {"rumor", "contradicted", "stale"}
    )
    state_file = PROJECT_ROOT / "outputs" / "report_state.json"
    files["report_state"] = state_file
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
    )
    files["stock_candidates"] = build_stock_candidates(PROJECT_ROOT / "outputs", data_dir, market_data)
    files["news_summary"] = write_news_summary(PROJECT_ROOT / "outputs")

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
    fields = ["标题", "摘要", "来源", "域名", "查询词", "A股相关性分数", "是否保留", "过滤原因"]
    rows: list[dict[str, str]] = []
    if source_path.exists():
        with source_path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                rows.append({field: str(row.get(field, "") or "") for field in fields})
    with target_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target_path


def save_report_state(path: Path, **state: Any) -> None:
    source_status = list(state.get("source_status", []))
    success_count = sum(1 for item in source_status if item.get("success"))
    source_success_rate = round(success_count / len(source_status), 4) if source_status else 0.0
    coverage_report = dict(state.get("coverage_report", {}) or {})
    payload = {
        "last_update_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": state.get("mode", ""),
        "raw_count": int(state.get("raw_count", 0)),
        "deduped_count": int(state.get("deduped_count", 0)),
        "high_confidence_count": int(state.get("high_confidence_count", 0)),
        "risk_announcement_count": int(state.get("risk_announcement_count", 0)),
        "risk_count": int(state.get("risk_count", 0)),
        "source_success_rate": source_success_rate,
        "source_status": source_status,
        "successful_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "success"],
        "failed_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "failed"],
        "timeout_sources": [str(item.get("source_name") or item.get("source") or "") for item in source_status if item.get("status") == "timeout"],
        "fallback_usage": list(coverage_report.get("fallback_usage", [])),
        "warnings": list(state.get("warnings", [])),
        "coverage_report": coverage_report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> dict[str, Path]:
    args = parse_args(argv or [])
    return run_pipeline(args.mode)


if __name__ == "__main__":
    main(sys.argv[1:])

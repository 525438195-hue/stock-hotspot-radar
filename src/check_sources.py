"""Check online source readiness and write a Chinese diagnostic report."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fetchers.market_fetcher import MarketFetcher
from source_config import configured_sources, load_source_config, secret


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "source_check.md"

MANUAL_FALLBACK_FILES = [
    "manual_news.csv",
    "manual_announcements.csv",
    "manual_market.csv",
    "manual_rumors.csv",
]


def main() -> None:
    sources_config = load_source_config(PROJECT_ROOT)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines = _build_report(sources_config)
    OUTPUT_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"已生成：{OUTPUT_PATH}", flush=True)


def _build_report(sources_config: dict[str, Any]) -> list[str]:
    env_path = PROJECT_ROOT / ".env"
    env_values = dict(sources_config.get("_env", {}))
    tavily_key = secret(env_values, "TAVILY_API_KEY")

    rss_sources = list(sources_config.get("rss_sources", []))
    empty_rss = [str(source.get("name") or "未命名 RSS 源") for source in rss_sources if not str(source.get("url", "")).strip()]
    placeholder_sources = [
        str(source.get("name") or "未命名官方源")
        for source in sources_config.get("official_sources", [])
        if str(source.get("mode", "")).strip() == "placeholder"
    ]
    akshare_status = _check_akshare()
    manual_status = _manual_fallback_status()

    lines = [
        "# 数据源检查报告",
        "",
        f"- 检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 说明：本检查只验证当前配置范围内的数据源可用性，不代表全网穷尽搜索。",
        "",
        "## 一、环境与 API Key",
        "",
        f"- .env 文件：{'存在' if env_path.exists() else '不存在'}",
        f"- TAVILY_API_KEY：{'已配置' if tavily_key else '未配置'}",
        f"- GOOGLE_API_KEY：{'已配置' if secret(env_values, 'GOOGLE_API_KEY') else '未配置'}",
        f"- GOOGLE_CSE_ID：{'已配置' if secret(env_values, 'GOOGLE_CSE_ID') else '未配置'}",
        f"- NEWSAPI_KEY：{'已配置' if secret(env_values, 'NEWSAPI_KEY') else '未配置'}",
        "",
        "## 二、联网搜索源",
        "",
    ]
    lines.extend(_source_status_lines(configured_sources(sources_config, "search_sources")))

    lines.extend(["", "## 三、RSS 新闻源", ""])
    if empty_rss:
        for name in empty_rss:
            lines.append(f"- {name}：跳过 RSS 源：URL 为空")
    else:
        lines.append("- RSS URL：均已配置")
    lines.extend(_source_status_lines(configured_sources(sources_config, "rss_sources")))

    lines.extend(["", "## 四、官方公告源", ""])
    if placeholder_sources:
        for name in placeholder_sources:
            lines.append(f"- {name}：占位源，未接入公开接口，不计为程序失败")
    else:
        lines.append("- 暂无占位官方源")
    lines.extend(_source_status_lines(configured_sources(sources_config, "official_sources")))

    lines.extend(
        [
            "",
            "## 五、AKShare 行情源",
            "",
            f"- 状态：{akshare_status['status']}",
            f"- 说明：{akshare_status['reason']}",
        ]
    )

    lines.extend(["", "## 六、manual fallback 文件", ""])
    for item in manual_status:
        lines.append(f"- {item['file']}：{item['status']}，有效行数 {item['rows']}，{item['reason']}")

    lines.extend(["", "## 七、结论", ""])
    if tavily_key or any(str(source.get("url", "")).strip() for source in rss_sources):
        lines.append("- 当前至少存在一个可尝试的联网新闻源。")
    else:
        lines.append("- 当前没有可用联网新闻源，请配置 Tavily API Key 或 RSS URL。")
    if not tavily_key:
        lines.append("- 跳过 Tavily：未配置 TAVILY_API_KEY。")
    if empty_rss:
        lines.append("- 存在 RSS 源 URL 为空，auto 模式会跳过这些源并继续运行。")
    if placeholder_sources:
        lines.append("- 巨潮、上交所、深交所等占位源会显示为占位跳过，不影响主流程。")
    if any(item["rows"] for item in manual_status):
        lines.append("- manual fallback 文件可用，联网失败时仍可生成报告。")

    return lines


def _source_status_lines(records: list[dict[str, Any]]) -> list[str]:
    if not records:
        return ["- 暂无配置"]
    status_labels = {"success": "可尝试", "failed": "失败", "skipped": "跳过", "timeout": "超时"}
    lines = []
    for record in records:
        status = str(record.get("status", "skipped"))
        reason = str(record.get("reason", ""))
        lines.append(
            "- "
            f"{record.get('source_name', '未命名数据源')}："
            f"{status_labels.get(status, status)}，"
            f"优先级 {record.get('priority', '')}，"
            f"{reason}"
        )
    return lines


def _check_akshare() -> dict[str, str]:
    try:
        result = MarketFetcher().safe_fetch()
    except Exception as exc:
        return {"status": "失败", "reason": f"AKShare 检查异常：{exc}"}

    status = str(result.get("status") or ("success" if result.get("success") else "failed"))
    if status == "success":
        items = result.get("items", {})
        count = len(items.get("sectors", [])) if isinstance(items, dict) else 0
        return {"status": "可用", "reason": f"AKShare 返回 {count} 条板块行情"}
    if status == "timeout":
        return {"status": "超时", "reason": "AKShare 超过 20 秒未返回，auto 模式会回退到 manual_market.csv"}
    return {"status": "失败", "reason": str(result.get("warning", "AKShare 未返回有效数据"))}


def _manual_fallback_status() -> list[dict[str, Any]]:
    statuses = []
    data_dir = PROJECT_ROOT / "data"
    for filename in MANUAL_FALLBACK_FILES:
        path = data_dir / filename
        rows = _csv_row_count(path)
        if not path.exists():
            status = "缺失"
            reason = "文件不存在"
        elif rows == 0:
            status = "为空"
            reason = "文件存在，但暂无有效数据行"
        else:
            status = "可用"
            reason = "可作为联网失败后的 fallback"
        statuses.append({"file": filename, "status": status, "rows": rows, "reason": reason})
    return statuses


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        if not path.read_text(encoding="utf-8-sig").strip():
            return 0
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            return sum(1 for row in reader if any(str(value or "").strip() for value in row.values()))
    except UnicodeDecodeError:
        with path.open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            return sum(1 for row in reader if any(str(value or "").strip() for value in row.values()))


if __name__ == "__main__":
    # Keep multiprocessing-based AKShare checks safely under the Windows main guard.
    os.environ.setdefault("PYTHONUTF8", "1")
    main()

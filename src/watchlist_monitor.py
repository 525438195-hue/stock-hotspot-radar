"""Rule-based watchlist intelligence monitor."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from result_quality_filter import annotate_results
from secrets_manager import runtime_secret
from source_config import load_source_config
from text_normalizer import normalize_text_with_flag
from time_utils import format_publish_time, format_publish_time_iso


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHLIST_FIELDS = ["股票名称", "股票代码", "所属题材", "关注级别", "持仓状态", "成本价", "备注"]
WATCHLIST_EXAMPLES = [
    {"股票名称": "内蒙一机", "股票代码": "600967", "所属题材": "军工", "关注级别": "高", "持仓状态": "持有", "成本价": "21", "备注": "关注军工催化和公告"},
    {"股票名称": "比亚迪", "股票代码": "002594", "所属题材": "新能源/军工", "关注级别": "中", "持仓状态": "观察", "成本价": "", "备注": "关注出口、储能、军工传闻"},
    {"股票名称": "南钢股份", "股票代码": "600282", "所属题材": "数据要素", "关注级别": "中", "持仓状态": "观察", "成本价": "", "备注": "关注数据资产和钢铁板块"},
]
WATCHLIST_NEWS_FIELDS = [
    "股票名称",
    "股票代码",
    "所属题材",
    "标题",
    "摘要",
    "来源",
    "来源类型",
    "发布时间",
    "发布时间_北京时间",
    "原始链接",
    "查询词",
    "消息类型",
    "是否保留",
    "过滤原因",
    "A股相关性分数",
    "风险标签",
]
WATCHLIST_REVIEW_FIELDS = [
    "股票名称",
    "股票代码",
    "所属题材",
    "关注级别",
    "持仓状态",
    "成本价",
    "新闻数量",
    "公告数量",
    "传闻数量",
    "风险数量",
    "行情异动数量",
    "最新消息标题",
    "最新消息时间",
    "情报状态",
    "规则观察建议",
    "核心理由",
    "风险提示",
    "观察条件",
    "放弃条件",
    "更新时间",
]
FORBIDDEN_REPLACEMENTS = {
    "买入": "观察",
    "卖出": "降低关注",
    "满仓": "风险较高",
    "梭哈": "风险较高",
    "必涨": "风险较高",
    "明天涨停": "风险较高",
    "稳赚": "风险较高",
}
MESSAGE_TYPES = ["正式新闻", "公告信息", "社媒传闻", "风险消息", "行情异动", "其他参考"]
MAJOR_RISK_TERMS = ["减持", "监管", "问询", "澄清", "否认", "处罚", "立案", "违规"]
SOCIAL_TERMS = ["股吧", "雪球", "抖音", "小道消息", "传闻"]
ANNOUNCEMENT_TERMS = ["公告", "巨潮", "上交所", "深交所", "澄清", "问询函"]
MARKET_TERMS = ["涨停", "异动", "龙虎榜", "放量", "资金"]
MAINSTREAM_TERMS = ["东方财富", "财联社", "同花顺", "证券时报", "第一财经", "证券日报", "中国证券报"]
LOW_QUALITY_DOMAINS = {"wikipedia.org", "zh.wikipedia.org", "baike.baidu.com", "cloudflare.com", "abb.com"}


def ensure_watchlist_template(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, WATCHLIST_FIELDS, WATCHLIST_EXAMPLES)


def load_watchlist(path: Path) -> list[dict[str, str]]:
    ensure_watchlist_template(path)
    rows = _read_csv(path)
    return [
        {field: str(row.get(field, "")).strip() for field in WATCHLIST_FIELDS}
        for row in rows
        if str(row.get("股票名称", "")).strip() or str(row.get("股票代码", "")).strip()
    ]


def run_watchlist_monitor(project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or PROJECT_ROOT
    data_dir = root / "data"
    output_dir = root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    watchlist = load_watchlist(data_dir / "watchlist.csv")
    config = load_source_config(root)
    news_rows: list[dict[str, str]] = []
    if watchlist:
        news_rows = _fetch_watchlist_news(watchlist, config, root)
    review_rows = _build_review_rows(watchlist, news_rows)
    _write_csv(output_dir / "watchlist_news.csv", WATCHLIST_NEWS_FIELDS, news_rows)
    _write_csv(output_dir / "watchlist_review.csv", WATCHLIST_REVIEW_FIELDS, review_rows)
    metrics = update_report_state(output_dir, watchlist, news_rows, review_rows)
    print(f"自选股情报监控完成：自选股 {len(watchlist)} 只，保留消息 {metrics['watchlist_news_count']} 条。")
    return metrics


def update_report_state(
    output_dir: Path,
    watchlist: list[dict[str, str]],
    news_rows: list[dict[str, str]],
    review_rows: list[dict[str, str]],
) -> dict[str, Any]:
    retained = [row for row in news_rows if row.get("是否保留") == "是"]
    metrics = {
        "watchlist_stock_count": len(watchlist),
        "watchlist_news_count": len(retained),
        "watchlist_rumor_count": sum(1 for row in retained if row.get("消息类型") == "社媒传闻"),
        "watchlist_risk_count": sum(1 for row in review_rows if row.get("情报状态") == "有风险"),
        "watchlist_last_update_time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state_path = output_dir / "report_state.json"
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
    state.update(metrics)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def metrics_from_existing_outputs(project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or PROJECT_ROOT
    watchlist = load_watchlist(root / "data" / "watchlist.csv")
    news_rows = _read_csv(root / "outputs" / "watchlist_news.csv")
    review_rows = _read_csv(root / "outputs" / "watchlist_review.csv")
    return update_report_state(root / "outputs", watchlist, news_rows, review_rows)


def _fetch_watchlist_news(
    watchlist: list[dict[str, str]],
    config: dict[str, Any],
    project_root: Path,
) -> list[dict[str, str]]:
    api_key = runtime_secret(project_root, "TAVILY_API_KEY")
    if not api_key:
        print("跳过自选股 Tavily：未配置 TAVILY_API_KEY。")
        return []
    raw_results: list[dict[str, Any]] = []
    query_count = 0
    for stock in watchlist:
        for query in _stock_queries(stock)[:8]:
            if query_count >= 60:
                break
            query_count += 1
            print(f"正在抓取：自选股 Tavily - {query}")
            raw_results.extend(_fetch_tavily_query(api_key, query, stock, config))
        if query_count >= 60:
            break
    annotated = annotate_results(
        raw_results,
        include_domains=_tavily_domains(config, "include_domains"),
        exclude_domains=_tavily_domains(config, "exclude_domains"),
    )
    deduped = _dedupe_news(annotated)
    return [_news_csv_row(result) for result in deduped]


def _fetch_tavily_query(api_key: str, query: str, stock: dict[str, str], config: dict[str, Any]) -> list[dict[str, Any]]:
    import requests  # type: ignore

    body = {
        "query": query,
        "max_results": 3,
        "search_depth": str(config.get("tavily_search", {}).get("search_depth", "basic")),
        "include_raw_content": False,
        "include_answer": False,
        "topic": "news",
        "country": str(config.get("tavily_search", {}).get("country", "china")),
        "exclude_domains": _tavily_domains(config, "exclude_domains"),
    }
    include_domains = _tavily_domains(config, "include_domains")
    if include_domains:
        body["include_domains"] = include_domains
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        if response.status_code == 400 and body.get("include_domains"):
            body.pop("include_domains", None)
            response = session.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=15,
            )
        response.raise_for_status()
    except Exception as exc:
        print(f"自选股 Tavily 失败：{query}，原因 {type(exc).__name__}: {exc}")
        return []
    payload = response.json()
    rows = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        rows.append(_normalize_result(item, query, stock))
    print(f"自选股 Tavily 成功：{query} 获取 {len(rows)} 条")
    return rows


def _normalize_result(item: dict[str, Any], query: str, stock: dict[str, str]) -> dict[str, Any]:
    title, title_changed = normalize_text_with_flag(item.get("title", ""))
    snippet, snippet_changed = normalize_text_with_flag(item.get("content") or item.get("snippet", ""))
    query_text, query_changed = normalize_text_with_flag(query)
    publish_time = str(item.get("published_date") or item.get("publishedAt") or "")
    url = str(item.get("url", ""))
    return {
        "股票名称": stock.get("股票名称", ""),
        "股票代码": stock.get("股票代码", ""),
        "所属题材": stock.get("所属题材", ""),
        "title": title.strip(),
        "snippet": snippet.strip(),
        "source": "Tavily Search API",
        "source_type": "搜索API",
        "publish_time": publish_time,
        "publish_time_iso": format_publish_time_iso(publish_time),
        "publish_time_beijing": format_publish_time(publish_time),
        "url": url,
        "domain": _domain(url),
        "query": query_text.strip(),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "simplified": title_changed or snippet_changed or query_changed,
    }


def _news_csv_row(result: dict[str, Any]) -> dict[str, str]:
    message_type = _message_type(result)
    risk_tags = _risk_tags(result)
    keep = "否" if _domain(str(result.get("url", ""))) in LOW_QUALITY_DOMAINS else str(result.get("keep", "否"))
    return {
        "股票名称": _safe(result.get("股票名称", "")),
        "股票代码": _safe(result.get("股票代码", "")),
        "所属题材": _safe(result.get("所属题材", "")),
        "标题": _safe(result.get("title", "")),
        "摘要": _safe(result.get("snippet", "")),
        "来源": _safe(result.get("source", "")),
        "来源类型": _safe(result.get("source_type", "")),
        "发布时间": _safe(result.get("publish_time", "")),
        "发布时间_北京时间": _safe(result.get("publish_time_beijing", "")),
        "原始链接": _safe(result.get("url", "")),
        "查询词": _safe(result.get("query", "")),
        "消息类型": message_type,
        "是否保留": keep,
        "过滤原因": _safe(result.get("filter_reason", "")),
        "A股相关性分数": str(result.get("a_share_score", "")),
        "风险标签": risk_tags,
    }


def _build_review_rows(watchlist: list[dict[str, str]], news_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    retained_by_stock: dict[str, list[dict[str, str]]] = {}
    for row in news_rows:
        if row.get("是否保留") != "是":
            continue
        retained_by_stock.setdefault(_stock_key(row), []).append(row)

    updated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    review_rows = []
    for stock in watchlist:
        rows = sorted(retained_by_stock.get(_stock_key(stock), []), key=lambda item: item.get("发布时间_北京时间", ""), reverse=True)
        counts = {message_type: sum(1 for row in rows if row.get("消息类型") == message_type) for message_type in MESSAGE_TYPES}
        risk_count = sum(1 for row in rows if row.get("消息类型") == "风险消息" or row.get("风险标签") not in {"", "无"})
        latest = rows[0] if rows else {}
        status = _intelligence_status(rows, counts, risk_count)
        suggestion = _review_suggestion(stock, counts, risk_count, status)
        review_rows.append(
            {
                "股票名称": stock.get("股票名称", ""),
                "股票代码": stock.get("股票代码", ""),
                "所属题材": stock.get("所属题材", ""),
                "关注级别": stock.get("关注级别", ""),
                "持仓状态": stock.get("持仓状态", ""),
                "成本价": stock.get("成本价", ""),
                "新闻数量": str(counts["正式新闻"]),
                "公告数量": str(counts["公告信息"]),
                "传闻数量": str(counts["社媒传闻"]),
                "风险数量": str(risk_count),
                "行情异动数量": str(counts["行情异动"]),
                "最新消息标题": latest.get("标题", ""),
                "最新消息时间": latest.get("发布时间_北京时间", ""),
                "情报状态": status,
                "规则观察建议": suggestion,
                "核心理由": _core_reason(stock, counts, risk_count, status),
                "风险提示": _risk_summary(rows),
                "观察条件": _watch_condition(stock, suggestion),
                "放弃条件": _abandon_condition(suggestion),
                "更新时间": updated_at,
            }
        )
    return [{key: _safe(row.get(key, "")) for key in WATCHLIST_REVIEW_FIELDS} for row in review_rows]


def _stock_queries(stock: dict[str, str]) -> list[str]:
    name = stock.get("股票名称", "").strip()
    code = stock.get("股票代码", "").strip()
    topic = stock.get("所属题材", "").strip()
    return [
        f"{name} {code} 今日",
        f"{name} 公告",
        f"{name} 东方财富",
        f"{name} 财联社",
        f"{name} 同花顺",
        f"{name} 股吧",
        f"{name} 减持",
        f"{name} 监管",
        f"{name} 澄清",
        f"{name} 龙虎榜",
        f"{name} 异动",
        f"{name} 订单",
        f"{name} {topic}",
    ]


def _message_type(result: dict[str, Any]) -> str:
    text = _combined_text(result)
    if any(term in text for term in MAJOR_RISK_TERMS):
        return "风险消息"
    if any(term in text for term in ANNOUNCEMENT_TERMS):
        return "公告信息"
    if any(term in text for term in SOCIAL_TERMS):
        return "社媒传闻"
    if any(term in text for term in MARKET_TERMS):
        return "行情异动"
    if any(term in text for term in MAINSTREAM_TERMS):
        return "正式新闻"
    return "其他参考"


def _risk_tags(result: dict[str, Any]) -> str:
    text = _combined_text(result)
    tags = []
    mapping = [
        ("减持", "减持风险"),
        ("监管", "监管风险"),
        ("问询", "问询风险"),
        ("澄清", "澄清风险"),
        ("否认", "公司否认"),
        ("处罚", "处罚风险"),
        ("立案", "立案风险"),
        ("传闻", "传闻风险"),
        ("小道消息", "传闻风险"),
    ]
    for keyword, label in mapping:
        if keyword in text and label not in tags:
            tags.append(label)
    return "；".join(tags) if tags else "无"


def _intelligence_status(rows: list[dict[str, str]], counts: dict[str, int], risk_count: int) -> str:
    if not rows:
        return "暂无新消息"
    if risk_count:
        return "有风险"
    if counts["社媒传闻"] and not counts["正式新闻"] and not counts["公告信息"]:
        return "仅有传闻"
    if rows:
        return "有新消息"
    return "信息不足"


def _review_suggestion(stock: dict[str, str], counts: dict[str, int], risk_count: int, status: str) -> str:
    if risk_count:
        return "降低关注" if stock.get("持仓状态") == "持有" else "直接排除"
    if status == "仅有传闻":
        return "等待确认"
    if status == "暂无新消息":
        return "信息不足"
    has_signal = counts["正式新闻"] or counts["公告信息"] or counts["行情异动"]
    if stock.get("持仓状态") == "持有" and has_signal:
        return "继续持有"
    if stock.get("关注级别") == "高" and has_signal:
        return "优先跟踪"
    if has_signal:
        return "等待确认"
    return "暂不参与"


def _core_reason(stock: dict[str, str], counts: dict[str, int], risk_count: int, status: str) -> str:
    if risk_count:
        return f"检索到 {risk_count} 条风险相关消息，需要人工优先复核。"
    if status == "暂无新消息":
        return "今日未检索到保留消息，信息不足。"
    if status == "仅有传闻":
        return "仅检索到传闻或社媒线索，缺少正式新闻或公告验证。"
    return f"检索到正式新闻 {counts['正式新闻']} 条、公告 {counts['公告信息']} 条、行情异动 {counts['行情异动']} 条。"


def _risk_summary(rows: list[dict[str, str]]) -> str:
    tags = []
    for row in rows:
        for tag in row.get("风险标签", "").split("；"):
            tag = tag.strip()
            if tag and tag != "无" and tag not in tags:
                tags.append(tag)
    return "；".join(tags) if tags else "无"


def _watch_condition(stock: dict[str, str], suggestion: str) -> str:
    if suggestion == "信息不足":
        return "等待正式新闻、公告或行情异动补充验证。"
    if suggestion == "等待确认":
        return "等待公告、主流财经媒体或交易所披露交叉验证。"
    return f"围绕{stock.get('股票名称', '')}继续观察公告、主流新闻、风险提示和题材催化。"


def _abandon_condition(suggestion: str) -> str:
    if suggestion == "直接排除":
        return "存在重大风险或事实冲突，不进入自选股重点观察。"
    return "出现公司否认、监管处罚、减持风险，或连续缺少新增验证时降低关注。"


def _tavily_domains(config: dict[str, Any], key: str) -> list[str]:
    values = config.get("tavily_search", {}).get(key, [])
    return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []


def _dedupe_news(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for row in rows:
        key = (row.get("股票代码"), row.get("url") or row.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _stock_key(row: dict[str, str]) -> str:
    return row.get("股票代码") or row.get("股票名称", "")


def _combined_text(row: dict[str, Any]) -> str:
    return f"{row.get('title', '')}\n{row.get('snippet', '')}\n{row.get('source', '')}\n{row.get('查询词', '')}"


def _domain(url: str) -> str:
    return urlparse(str(url or "")).netloc.lower().removeprefix("www.")


def _safe(value: object) -> str:
    text = str(value or "")
    for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
        text = text.replace(forbidden, replacement)
    return text


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])


def main() -> None:
    run_watchlist_monitor(PROJECT_ROOT)


if __name__ == "__main__":
    main()

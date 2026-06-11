"""Build stock and theme observation candidates from search results."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from stock_extractor import extract_stocks_from_text, is_placeholder_name, load_stock_code_map, stock_from_leading_name


CANDIDATE_FIELDS = [
    "候选类型",
    "股票名称",
    "股票代码",
    "所属题材",
    "题材强度分",
    "可信度分数",
    "市场信号",
    "信息来源",
    "验证状态",
    "风险标签",
    "观察建议",
    "观察条件",
    "放弃条件",
    "相关新闻标题",
    "发布时间",
    "原始链接",
    "备注",
]
POSITIVE_SUGGESTIONS = {"优先跟踪", "只看核心", "等待回踩"}
MAJOR_RISKS = {"公司否认", "监管风险", "减持风险", "明确虚假消息"}
FORBIDDEN_REPLACEMENTS = {
    "买入": "观察",
    "卖出": "降低权重",
    "推荐买": "观察",
    "满仓": "风险较高",
    "梭哈": "风险较高",
    "必涨": "风险较高",
    "明天涨停": "风险较高",
    "稳赚": "风险较高",
}


def build_stock_candidates(
    output_dir: Path,
    data_dir: Path,
    market_data: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    code_map = load_stock_code_map(data_dir / "a_stock_code_map.csv")
    search_rows = _read_csv(output_dir / "search_results_deduped.csv")
    risk_rows = _read_csv(output_dir / "risk_flags.csv")
    state = _read_json(output_dir / "report_state.json")
    market_rows = _market_rows(market_data, data_dir)
    market_by_topic = {row.get("题材", ""): row for row in market_rows if row.get("题材")}
    risk_text = "\n".join(" ".join(row.values()) for row in risk_rows)

    candidates: list[dict[str, str]] = []
    for row in search_rows:
        if row.get("是否保留") != "是":
            continue
        topic = row.get("题材") or _topic_from_query(row.get("查询词", "")) or "未识别题材"
        result_type = row.get("结果类型") or "题材参考"
        market = market_by_topic.get(topic, {})
        text = f"{row.get('标题', '')}\n{row.get('摘要', '')}\n{row.get('来源', '')}"
        stocks = extract_stocks_from_text(text, code_map)
        if stocks and result_type == "高质量新闻":
            for stock in stocks:
                risk_tags = _risk_tags_for(stock.get("股票名称", ""), risk_text)
                candidates.append(
                    _candidate_row(
                        candidate_type=stock.get("候选类型", "个股待补代码"),
                        stock_name=stock.get("股票名称", ""),
                        stock_code=stock.get("股票代码", ""),
                        topic=topic,
                        confidence=_confidence(row, result_type),
                        strength=_theme_strength(market, row),
                        market=market,
                        source=row.get("来源", ""),
                        verification="部分确认" if result_type == "高质量新闻" else "仅题材参考",
                        risk_tags=risk_tags,
                        news_title=row.get("标题", ""),
                        publish_time=row.get("发布时间_北京时间") or row.get("发布时间", ""),
                        url=row.get("原始链接", ""),
                        result_type=result_type,
                        state=state,
                    )
                )
        else:
            candidates.append(
                _candidate_row(
                    candidate_type="题材",
                    stock_name="",
                    stock_code="",
                    topic=topic,
                    confidence=_confidence(row, result_type),
                    strength=_theme_strength(market, row),
                    market=market,
                    source=row.get("来源", ""),
                    verification="题材参考" if result_type == "题材参考" else "部分确认",
                    risk_tags="",
                    news_title=row.get("标题", ""),
                    publish_time=row.get("发布时间_北京时间") or row.get("发布时间", ""),
                    url=row.get("原始链接", ""),
                    result_type=result_type,
                    state=state,
                )
            )

    for market in market_rows:
        topic = market.get("题材", "")
        leading = stock_from_leading_name(market.get("领涨股票", ""), code_map)
        if leading:
            risk_tags = _risk_tags_for(leading.get("股票名称", ""), risk_text)
            candidates.append(
                _candidate_row(
                    candidate_type=leading.get("候选类型", "个股待补代码"),
                    stock_name=leading.get("股票名称", ""),
                    stock_code=leading.get("股票代码", ""),
                    topic=topic,
                    confidence=62,
                    strength=_theme_strength(market, {}),
                    market=market,
                    source="manual_market.csv",
                    verification="仅有市场反应",
                    risk_tags=risk_tags,
                    news_title="",
                    publish_time="",
                    url="",
                    result_type="行情补充",
                    state=state,
                )
            )
        elif topic and not any(row.get("所属题材") == topic for row in candidates):
            candidates.append(
                _candidate_row(
                    candidate_type="题材",
                    stock_name="",
                    stock_code="",
                    topic=topic,
                    confidence=55,
                    strength=_theme_strength(market, {}),
                    market=market,
                    source="manual_market.csv",
                    verification="仅有市场反应",
                    risk_tags="",
                    news_title="",
                    publish_time="",
                    url="",
                    result_type="行情补充",
                    state=state,
                )
            )

    candidates = _merge_duplicate_rows([row for row in candidates if row.get("候选类型") != "占位"])
    candidates.sort(key=_sort_key)
    path = output_dir / "stock_candidates.csv"
    if not candidates and path.exists():
        return path
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows([{field: _sanitize(_compress_field(field, row.get(field, ""))) for field in CANDIDATE_FIELDS} for row in candidates])
    return path


def _candidate_row(
    *,
    candidate_type: str,
    stock_name: str,
    stock_code: str,
    topic: str,
    confidence: int,
    strength: int,
    market: dict[str, str],
    source: str,
    verification: str,
    risk_tags: str,
    news_title: str,
    publish_time: str,
    url: str,
    result_type: str,
    state: dict[str, Any],
) -> dict[str, str]:
    if is_placeholder_name(stock_name):
        candidate_type = "占位"
    suggestion = _suggestion(candidate_type, confidence, strength, risk_tags, result_type)
    return {
        "候选类型": candidate_type,
        "股票名称": stock_name,
        "股票代码": stock_code,
        "所属题材": topic,
        "题材强度分": str(strength),
        "可信度分数": str(confidence),
        "市场信号": _market_signal_text(market),
        "信息来源": source,
        "验证状态": verification,
        "风险标签": risk_tags or "无",
        "观察建议": suggestion,
        "观察条件": _observation_condition(candidate_type, topic, stock_name, suggestion),
        "放弃条件": _abandon_condition(risk_tags, suggestion),
        "相关新闻标题": news_title,
        "发布时间": publish_time or "时间未知",
        "原始链接": url,
        "备注": f"{result_type}；更新时间：{state.get('last_update_time', '暂无')}",
    }


def _suggestion(candidate_type: str, confidence: int, strength: int, risk_tags: str, result_type: str) -> str:
    if any(risk in risk_tags for risk in MAJOR_RISKS):
        return "直接排除"
    if "只有社媒" in risk_tags or "未证实传闻" in risk_tags:
        return "直接排除"
    has_real_stock = candidate_type in {"个股", "个股待补代码"}
    if has_real_stock and result_type == "高质量新闻" and strength >= 70 and confidence >= 70:
        return "优先跟踪"
    if has_real_stock and strength >= 60:
        return "只看核心"
    if has_real_stock and ("高位追涨风险" in risk_tags or strength >= 75):
        return "等待回踩"
    if candidate_type == "题材" and confidence >= 45:
        return "暂不参与"
    return "暂不参与"


def _observation_condition(candidate_type: str, topic: str, stock_name: str, suggestion: str) -> str:
    target = stock_name or topic
    if suggestion == "优先跟踪":
        return f"观察{target}是否继续获得高质量新闻、公告或资金验证，重点看题材持续性和风险公告"
    if suggestion == "只看核心":
        return f"只观察{topic}中来源清晰、流动性更好的核心标的，等待更多验证"
    if suggestion == "等待回踩":
        return f"等待{target}分歧、回踩和承接确认，不追逐情绪扩散"
    if candidate_type == "题材":
        return f"{topic}仅作为题材观察，等待明确上市公司、代码或公告线索"
    return f"继续核验{target}与{topic}的关系"


def _abandon_condition(risk_tags: str, suggestion: str) -> str:
    if suggestion == "直接排除":
        return "存在重大风险或事实冲突，不进入观察池"
    return "出现公司否认、监管风险、减持风险，或题材新闻连续缺少新增验证"


def _confidence(row: dict[str, str], result_type: str) -> int:
    score = _int(row.get("A股相关性分数"))
    if result_type == "高质量新闻":
        return max(65, min(90, score))
    if result_type == "题材参考":
        return max(45, min(68, score))
    return max(30, min(55, score))


def _theme_strength(market: dict[str, str], row: dict[str, str]) -> int:
    if market:
        change = _number(market.get("板块涨幅"))
        limit_up = _int(market.get("涨停数量"))
        volume = _number(market.get("放量幅度"))
        return max(0, min(100, int(round(change * 10 + limit_up * 4 + max(volume, 0) * 0.5))))
    return max(30, min(70, _int(row.get("A股相关性分数"))))


def _market_rows(market_data: dict[str, Any] | None, data_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if market_data and market_data.get("sectors"):
        for item in market_data.get("sectors", []):
            rows.append(
                {
                    "题材": str(item.get("sector", "")),
                    "板块涨幅": str(item.get("change_pct", "")),
                    "涨停数量": str(item.get("limit_up_count", "")),
                    "成交额": str(item.get("turnover_amount_billion", "")),
                    "放量幅度": str(item.get("turnover_change_pct", "")),
                    "领涨股票": str(item.get("leading_stock", "")),
                }
            )
        return rows
    for row in _read_csv(data_dir / "manual_market.csv"):
        rows.append(
            {
                "题材": row.get("板块名称", ""),
                "板块涨幅": row.get("板块涨幅", ""),
                "涨停数量": row.get("涨停数量", ""),
                "成交额": row.get("成交额", ""),
                "放量幅度": row.get("放量幅度", ""),
                "领涨股票": row.get("领涨股票", ""),
            }
        )
    return rows


def _market_signal_text(market: dict[str, str]) -> str:
    if not market:
        return "暂无行情验证"
    return (
        f"板块涨幅 {market.get('板块涨幅', '')}；"
        f"涨停数量 {market.get('涨停数量', '')}；"
        f"成交额 {market.get('成交额', '')}；"
        f"放量幅度 {market.get('放量幅度', '')}"
    )


def _risk_tags_for(stock_name: str, risk_text: str) -> str:
    if not stock_name or stock_name not in risk_text:
        return ""
    tags = [risk for risk in MAJOR_RISKS if risk in risk_text]
    return "；".join(tags)


def _merge_duplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _dedupe_key(row)
        if key not in grouped:
            grouped[key] = dict(row)
        else:
            grouped[key] = _merge_row(grouped[key], row)
    return list(grouped.values())


def _dedupe_key(row: dict[str, str]) -> str:
    if row.get("候选类型") in {"个股", "个股待补代码"}:
        if row.get("股票代码"):
            return f"code:{row['股票代码']}"
        return f"name:{row.get('股票名称', '')}"
    return f"theme:{row.get('所属题材', '')}"


def _merge_row(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    merged = dict(left)
    for field in ["可信度分数", "题材强度分"]:
        merged[field] = str(max(_int(left.get(field)), _int(right.get(field))))
    for field in ["信息来源", "风险标签", "相关新闻标题", "原始链接", "备注"]:
        merged[field] = _merge_text(left.get(field, ""), right.get(field, ""))
    if not merged.get("发布时间") or merged.get("发布时间") == "时间未知":
        merged["发布时间"] = right.get("发布时间", merged.get("发布时间", ""))
    if _suggestion_rank(right.get("观察建议", "")) < _suggestion_rank(left.get("观察建议", "")):
        merged["观察建议"] = right.get("观察建议", merged.get("观察建议", ""))
        merged["观察条件"] = right.get("观察条件", merged.get("观察条件", ""))
    if merged.get("候选类型") == "个股待补代码" and right.get("候选类型") == "个股":
        merged["候选类型"] = "个股"
        merged["股票代码"] = right.get("股票代码", "")
    return merged


def _merge_text(left: str, right: str) -> str:
    values = []
    for value in [left, right]:
        for part in re.split(r"[；;|]+", str(value or "")):
            text = part.strip()
            if text and text not in values and text != "无":
                values.append(text)
    return "；".join(values[:4]) if values else "无"


def _compress_field(field: str, value: object) -> str:
    text = str(value or "")
    if field not in {"观察条件", "放弃条件", "备注", "市场信号"}:
        return text
    return _merge_text(text, "")


def _sort_key(row: dict[str, str]) -> tuple[int, int, int, str]:
    type_order = {"个股": 0, "个股待补代码": 1, "题材": 2, "占位": 3}
    suggestion_order = {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}
    return (
        type_order.get(row.get("候选类型", ""), 9),
        suggestion_order.get(row.get("观察建议", ""), 9),
        -_int(row.get("可信度分数")),
        row.get("所属题材", ""),
    )


def _topic_from_query(query: str) -> str:
    for topic in ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "新能源", "消费电子", "医药", "证券"]:
        if topic in str(query):
            return topic
    return str(query).split()[0] if query else ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _number(value: object) -> float:
    text = str(value or "").replace(",", "").replace("%", "").replace("亿元", "").replace("亿", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _int(value: object) -> int:
    return int(round(_number(value)))


def _suggestion_rank(value: str) -> int:
    return {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}.get(value, 9)


def _sanitize(value: object) -> str:
    text = str(value or "")
    for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
        text = text.replace(forbidden, replacement)
    return text


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    build_stock_candidates(root / "outputs", root / "data")

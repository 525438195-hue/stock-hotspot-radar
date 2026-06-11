"""Build Chinese stock observation candidates from pipeline outputs."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


CANDIDATE_FIELDS = [
    "候选类型",
    "股票名称",
    "股票代码",
    "所属题材",
    "题材阶段",
    "题材强度分",
    "可信度分数",
    "市场信号",
    "信息来源",
    "验证状态",
    "风险标签",
    "观察条件",
    "观察建议",
    "放弃条件",
    "备注",
]

MAJOR_RISKS = {"公司否认", "减持风险", "监管风险"}
SOCIAL_HINTS = {"社媒情绪", "仅社媒消息", "未证实传闻"}
POSITIVE_SUGGESTIONS = {"优先跟踪", "只看核心", "等待回踩"}
PLACEHOLDER_NAMES = {"某某机器人", "某某科技", "某某航空", "某某股份", "某某公司"}
TYPE_ORDER = {"个股": 0, "题材": 1, "占位": 2}
INVALID_STOCK_NAME_PARTS = {
    "今日",
    "多股",
    "人气股",
    "概念股",
    "产业链",
    "全产业",
    "再度",
    "板块",
    "上市公司",
    "股票",
    "A股",
}
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
    watchlist = _read_csv(output_dir / "watchlist.csv")
    risk_flags = _read_csv(output_dir / "risk_flags.csv")
    state = _read_json(output_dir / "report_state.json")
    market_by_topic = _market_by_topic(market_data or _read_manual_market(data_dir / "manual_market.csv"))
    risk_by_id = _risk_by_id(risk_flags)

    rows = []
    for item in watchlist:
        topic = item.get("题材") or item.get("对应板块") or "未识别题材"
        market = market_by_topic.get(topic, _market_from_watchlist(item))
        risk_tags = _merge_risk_tags(item.get("风险标签", ""), risk_by_id.get(item.get("事件编号", ""), []))
        score = _int(item.get("可信度分数"))
        strength = _theme_strength(market)
        suggestion = _suggestion(item, risk_tags, strength)
        stock_name = _candidate_stock_name(item, market)
        stock_code = _extract_stock_code(" ".join([stock_name, item.get("热点标题", ""), item.get("原始链接", "")]))
        candidate_type = _candidate_type(stock_name, stock_code, topic)
        rows.append(
            {
                "候选类型": candidate_type,
                "股票名称": _sanitize(stock_name),
                "股票代码": stock_code,
                "所属题材": _sanitize(topic),
                "题材阶段": _theme_stage(strength, _int(market.get("limit_up_count")), risk_tags),
                "题材强度分": str(strength),
                "可信度分数": str(score),
                "市场信号": _sanitize(_market_signal_text(market)),
                "信息来源": _sanitize(f"{item.get('来源', '')}（{item.get('来源类型', '')}）"),
                "验证状态": _sanitize(item.get("验证状态", "")),
                "风险标签": _sanitize(risk_tags or "无"),
                "观察条件": _sanitize(_observation_condition(item, market, suggestion)),
                "观察建议": suggestion,
                "放弃条件": _sanitize(_abandon_condition(item, risk_tags, suggestion)),
                "备注": _sanitize(_note(item, state, suggestion)),
            }
        )

    rows = _merge_duplicate_rows(rows)
    rows.sort(key=_sort_key)
    path = output_dir / "stock_candidates.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


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


def _read_manual_market(path: Path) -> dict[str, Any]:
    rows = _read_csv(path)
    sectors = []
    for row in rows:
        sectors.append(
            {
                "sector": row.get("板块名称", ""),
                "change_pct": _number(row.get("板块涨幅")),
                "limit_up_count": _int(row.get("涨停数量")),
                "turnover_amount_billion": _number(row.get("成交额")),
                "turnover_change_pct": _number(row.get("放量幅度")),
                "leading_stock": row.get("领涨股票", ""),
                "leading_stock_change_pct": _number(row.get("领涨股票涨幅")),
            }
        )
    return {"sectors": sectors}


def _market_by_topic(market_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("sector", "")): dict(item) for item in market_data.get("sectors", []) if item.get("sector")}


def _market_from_watchlist(row: dict[str, str]) -> dict[str, Any]:
    return {
        "sector": row.get("对应板块") or row.get("题材", ""),
        "change_pct": _number(row.get("板块涨幅")),
        "limit_up_count": _int(row.get("涨停数量")),
        "turnover_amount_billion": _number(row.get("成交额")),
        "turnover_change_pct": _number(row.get("放量幅度")),
        "leading_stock": "",
        "leading_stock_change_pct": 0,
    }


def _risk_by_id(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        key = row.get("编号", "")
        risk = row.get("风险类型", "")
        if key and risk:
            grouped.setdefault(key, []).append(risk)
    return grouped


def _merge_risk_tags(existing: str, extra: list[str]) -> str:
    tags = []
    for value in [existing, *extra]:
        for part in re.split(r"[；;、,，\s]+", value or ""):
            part = part.strip()
            if part and part != "无" and part not in tags:
                tags.append(part)
    return "；".join(tags)


def _suggestion(row: dict[str, str], risk_tags: str, strength: int) -> str:
    score = _int(row.get("可信度分数"))
    status = row.get("验证状态", "")
    source = row.get("来源类型", "")
    limit_up = _int(row.get("涨停数量"))
    has_major_risk = any(risk in risk_tags for risk in MAJOR_RISKS)
    social_only = any(hint in source or hint in risk_tags or hint in status for hint in SOCIAL_HINTS)
    reliable_or_market = source in {"财经媒体", "公司公告", "交易所公告", "政策文件", "行业媒体"} or strength >= 45

    if score < 40 or social_only or has_major_risk or status == "被否定":
        return "直接排除"
    if score >= 75 and status in {"已确认", "部分确认"} and reliable_or_market and strength >= 65 and not risk_tags:
        return "优先跟踪"
    if score >= 65 and strength >= 70 and limit_up >= 6:
        return "只看核心"
    if score >= 55 and (strength >= 75 or "高位追涨风险" in risk_tags):
        return "等待回踩"
    if 40 <= score <= 54:
        return "暂不参与"
    return "暂不参与"


def _candidate_stock_name(row: dict[str, str], market: dict[str, Any]) -> str:
    title = row.get("热点标题", "")
    source = row.get("来源", "")
    extracted = _extract_stock_name(title)
    if extracted:
        return extracted
    extracted = _extract_stock_name(source)
    if extracted:
        return extracted
    leading = _clean_stock_name(str(market.get("leading_stock", "") or ""))
    if leading:
        return leading
    return ""


def _candidate_type(stock_name: str, stock_code: str, topic: str) -> str:
    del topic
    if _is_placeholder(stock_name):
        return "占位"
    if stock_name or stock_code:
        return "个股"
    return "题材"


def _is_placeholder(stock_name: str) -> bool:
    return "某某" in stock_name or stock_name in PLACEHOLDER_NAMES


def _clean_stock_name(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\b(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}(?:\.(?:SZ|SH))?\b", "", text, flags=re.I)
    text = re.sub(r"^(千亿|百亿|行业龙头|龙头|核心股|人气股|今日)", "", text)
    text = re.sub(r"等.*$", "", text)
    text = re.sub(r"(紧急|午后|今日|盘中)$", "", text)
    text = re.sub(r"[（）()：:，,;；\s]+$", "", text).strip()
    return text


def _extract_stock_name(text: str) -> str:
    value = str(text or "")
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z]{2,10})[（(]\s*(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}",
        r"(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}[）)]?\s*([\u4e00-\u9fa5A-Za-z]{2,10})",
        r"([\u4e00-\u9fa5]{2,6})等(?:紧急|发布|公告|回应)",
        r"([\u4e00-\u9fa5]{2,6})触及涨停",
        r"([\u4e00-\u9fa5]{2,6})(?:股价|大涨|逆市|收涨|收跌|公告)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            candidate = _clean_stock_name(match.group(1))
            if _looks_like_real_stock_name(candidate):
                return candidate
    return ""


def _looks_like_real_stock_name(candidate: str) -> bool:
    if len(candidate) < 2 or len(candidate) > 8:
        return False
    if candidate in {"今日A股", "A股三大", "概念股", "上市公司", "证券时报", "东方财富", "财联社"}:
        return False
    if any(part in candidate for part in INVALID_STOCK_NAME_PARTS):
        return False
    return True


def _merge_duplicate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _dedupe_key(row)
        if key not in grouped:
            grouped[key] = dict(row)
            continue
        grouped[key] = _merge_candidate_row(grouped[key], row)
    return list(grouped.values())


def _dedupe_key(row: dict[str, str]) -> str:
    candidate_type = row.get("候选类型", "")
    code = row.get("股票代码", "").strip()
    name = row.get("股票名称", "").strip()
    topic = row.get("所属题材", "").strip()
    if candidate_type == "个股" and code:
        return f"stock-code:{code}"
    if candidate_type == "个股" and name:
        return f"stock-name:{name}"
    if candidate_type == "占位" and name:
        return f"placeholder:{name}:{topic}"
    return f"theme:{topic}"


def _merge_candidate_row(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    merged = dict(left)
    for field in ["可信度分数", "题材强度分"]:
        merged[field] = str(max(_int(left.get(field)), _int(right.get(field))))
    merged["信息来源"] = _merge_text_values(left.get("信息来源", ""), right.get("信息来源", ""))
    merged["风险标签"] = _merge_text_values(left.get("风险标签", ""), right.get("风险标签", ""), empty_value="无")
    merged["观察条件"] = _merge_summary(left.get("观察条件", ""), right.get("观察条件", ""))
    merged["备注"] = _merge_summary(left.get("备注", ""), right.get("备注", ""))
    if TYPE_ORDER.get(right.get("候选类型", ""), 9) < TYPE_ORDER.get(left.get("候选类型", ""), 9):
        merged["候选类型"] = right.get("候选类型", left.get("候选类型", ""))
    if _suggestion_rank(right.get("观察建议", "")) < _suggestion_rank(left.get("观察建议", "")):
        merged["观察建议"] = right.get("观察建议", left.get("观察建议", ""))
        merged["放弃条件"] = right.get("放弃条件", left.get("放弃条件", ""))
    return merged


def _merge_text_values(left: str, right: str, empty_value: str = "") -> str:
    values: list[str] = []
    for raw in [left, right]:
        for part in re.split(r"[；;、,，]+", raw or ""):
            text = part.strip()
            if not text or text == empty_value:
                continue
            if text not in values:
                values.append(text)
    return "；".join(values) if values else empty_value


def _merge_summary(left: str, right: str) -> str:
    values = [value for value in dict.fromkeys([left.strip(), right.strip()]) if value]
    return "；".join(values[:2])


def _suggestion_rank(value: str) -> int:
    return {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}.get(value, 9)


def _theme_strength(market: dict[str, Any]) -> int:
    change = _number(market.get("change_pct"))
    limit_up = _int(market.get("limit_up_count"))
    volume = _number(market.get("turnover_change_pct"))
    score = round(change * 10 + limit_up * 4 + max(volume, 0) * 0.5)
    return max(0, min(100, int(score)))


def _theme_stage(strength: int, limit_up_count: int, risk_tags: str) -> str:
    if any(risk in risk_tags for risk in MAJOR_RISKS):
        return "风险降权"
    if strength >= 80 or limit_up_count >= 8:
        return "强势扩散"
    if strength >= 60 or limit_up_count >= 5:
        return "主线发酵"
    if strength >= 40:
        return "观察初期"
    return "持续性待核验"


def _market_signal_text(market: dict[str, Any]) -> str:
    return (
        f"板块涨幅 {_number(market.get('change_pct')):.1f}%；"
        f"涨停数量 {_int(market.get('limit_up_count'))}；"
        f"成交额 {_number(market.get('turnover_amount_billion')):.1f}亿元；"
        f"放量幅度 {_number(market.get('turnover_change_pct')):.1f}%"
    )


def _observation_condition(row: dict[str, str], market: dict[str, Any], suggestion: str) -> str:
    topic = row.get("题材", "相关题材")
    if suggestion == "优先跟踪":
        return f"继续核验{topic}是否有新增公告、政策或成交额配合，观察领涨股承接强度"
    if suggestion == "只看核心":
        return f"只观察{topic}领涨股、中军和成交额最集中的方向，后排仅作情绪参考"
    if suggestion == "等待回踩":
        return f"等待{topic}分歧、回踩和承接确认，观察放量后是否仍有资金回流"
    if suggestion == "直接排除":
        return "仅保留为风险复核线索，不进入高可信观察池"
    return f"等待{topic}出现更清晰的来源验证或行情确认"


def _abandon_condition(row: dict[str, str], risk_tags: str, suggestion: str) -> str:
    if suggestion == "直接排除":
        return "已触发排除条件，除非出现官方澄清或风险解除，否则不进入观察池"
    conditions = ["出现公司否认、减持风险或监管风险", "题材强度下降且缺少新增可信来源"]
    if "高位追涨风险" in risk_tags:
        conditions.append("高位扩散后承接不足")
    return "；".join(conditions)


def _note(row: dict[str, str], state: dict[str, Any], suggestion: str) -> str:
    update_time = state.get("last_update_time", "暂无更新时间")
    return f"{suggestion}；更新时间：{update_time}；仅用于人工复核"


def _sort_key(row: dict[str, str]) -> tuple[int, int, str]:
    suggestion_order = {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}
    return (
        TYPE_ORDER.get(row.get("候选类型", ""), 9),
        suggestion_order.get(row["观察建议"], 9),
        -_int(row["可信度分数"]),
        row["所属题材"],
    )


def _number(value: object) -> float:
    text = str(value or "").replace(",", "").replace("%", "").replace("亿元", "").replace("亿", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _int(value: object) -> int:
    return int(round(_number(value)))


def _extract_stock_code(value: str) -> str:
    match = re.search(r"\b[036]\d{5}(?:\.(?:SZ|SH))?\b", value, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _sanitize(value: object) -> str:
    text = str(value or "")
    for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
        text = text.replace(forbidden, replacement)
    return text


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    build_stock_candidates(root / "outputs", root / "data")

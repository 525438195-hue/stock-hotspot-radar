"""Load manually maintained Chinese CSV files into pipeline dictionaries."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from text_normalizer import normalize_text


NEWS_HEADERS = ["标题", "正文", "来源", "来源类型", "发布时间", "原始链接", "相关关键词"]
ANNOUNCEMENT_HEADERS = ["公司名称", "股票代码", "公告标题", "公告正文", "公告类型", "发布时间", "原始链接"]
MARKET_HEADERS = ["板块名称", "板块涨幅", "涨停数量", "成交额", "放量幅度", "领涨股票", "领涨股票涨幅"]

SOURCE_TYPE_MAP = {
    "政策文件": "government_policy",
    "政府政策": "government_policy",
    "公司公告": "official_announcement",
    "官方公告": "official_announcement",
    "交易所公告": "exchange_announcement",
    "交易所披露": "exchange_announcement",
    "财经媒体": "financial_news",
    "财经新闻": "financial_news",
    "行业媒体": "industry_news",
    "行业新闻": "industry_news",
    "社媒情绪": "social_sentiment",
    "社媒": "social_sentiment",
    "股吧": "social_sentiment",
    "截图": "social_sentiment",
    "行情数据": "market_data",
    "市场数据": "market_data",
    "未知来源": "unknown",
    "未知": "unknown",
}

ANNOUNCEMENT_TYPE_MAP = {
    "澄清不涉及某业务": "clarification_no_business",
    "澄清公告": "clarification_no_business",
    "公司否认": "clarification_no_business",
    "减持公告": "shareholder_reduction",
    "减持": "shareholder_reduction",
    "监管函": "regulatory_letter",
    "监管警示": "regulatory_letter",
    "问询函": "regulatory_letter",
    "正面业务公告": "positive_business",
    "业务公告": "positive_business",
    "业绩预告": "performance_forecast",
}


def _read_csv_rows(path: Path, required_headers: list[str]) -> list[dict[str, str]]:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return []

    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            return []

        missing = [header for header in required_headers if header not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path.name} 缺少字段：{', '.join(missing)}")

        rows = []
        for row in reader:
            normalized = {key: normalize_text(str(row.get(key, "") or "").strip()) for key in required_headers}
            if any(normalized.values()):
                rows.append(normalized)
        return rows


def _normalize_source_type(value: str) -> str:
    return SOURCE_TYPE_MAP.get(value.strip(), "unknown")


def _normalize_announcement_type(value: str) -> str:
    return ANNOUNCEMENT_TYPE_MAP.get(value.strip(), value.strip() or "other")


def _normalize_publish_time(value: str) -> str:
    text = value.strip()
    if not text:
        return ""

    candidates = [
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in candidates:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in {"%Y-%m-%d", "%Y/%m/%d"}:
                return parsed.strftime("%Y-%m-%dT00:00:00+08:00")
            return parsed.strftime("%Y-%m-%dT%H:%M:00+08:00")
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text


def _parse_number(value: str) -> float:
    text = value.strip().replace(",", "")
    text = text.replace("%", "").replace("亿元", "").replace("亿", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_int(value: str) -> int:
    return int(round(_parse_number(value)))


def _split_keywords(value: str) -> list[str]:
    parts = re.split(r"[,，;；、\s]+", value)
    return [part.strip() for part in parts if part.strip()]


def _extract_tickers(*values: str) -> list[str]:
    text = " ".join(values)
    matches = re.findall(r"\b(?:[036]\d{5}(?:\.(?:SZ|SH))?)\b", text, flags=re.IGNORECASE)
    return sorted({match.upper() for match in matches})


def _topic_hint(keywords: str, title: str, content: str) -> str:
    for keyword in _split_keywords(keywords):
        if not re.fullmatch(r"[036]\d{5}(?:\.(?:SZ|SH))?", keyword, flags=re.IGNORECASE):
            return keyword
    text = f"{title}\n{content}"
    for topic in ["低空经济", "AI算力", "机器人", "半导体", "新能源", "医药", "数据要素"]:
        if topic in text:
            return topic
    return ""


def load_manual_news(path: Path) -> list[dict[str, Any]]:
    rows = _read_csv_rows(path, NEWS_HEADERS)
    events = []
    for index, row in enumerate(rows, start=1):
        events.append(
            {
                "event_id": f"MANUAL_NEWS_{index:03d}",
                "title": row["标题"],
                "content": row["正文"],
                "topic_hint": _topic_hint(row["相关关键词"], row["标题"], row["正文"]),
                "tickers": _extract_tickers(row["标题"], row["正文"], row["相关关键词"]),
                "source": row["来源"] or "手动导入",
                "source_type": _normalize_source_type(row["来源类型"]),
                "publish_time": _normalize_publish_time(row["发布时间"]),
                "url": row["原始链接"],
                "duplicate_count": 1,
            }
        )
    return events


def load_manual_announcements(path: Path) -> list[dict[str, Any]]:
    rows = _read_csv_rows(path, ANNOUNCEMENT_HEADERS)
    announcements = []
    for index, row in enumerate(rows, start=1):
        announcements.append(
            {
                "announcement_id": f"MANUAL_ANN_{index:03d}",
                "company": row["公司名称"],
                "ticker": row["股票代码"].upper(),
                "title": row["公告标题"],
                "content": row["公告正文"],
                "announcement_type": _normalize_announcement_type(row["公告类型"]),
                "source": row["公司名称"] or "手动公告",
                "source_type": "official_announcement",
                "publish_time": _normalize_publish_time(row["发布时间"]),
                "url": row["原始链接"],
            }
        )
    return announcements


def load_manual_market(path: Path) -> dict[str, Any]:
    rows = _read_csv_rows(path, MARKET_HEADERS)
    sectors = []
    for row in rows:
        sector_name = row["板块名称"]
        if not sector_name:
            continue
        sectors.append(
            {
                "sector": sector_name,
                "change_pct": _parse_number(row["板块涨幅"]),
                "limit_up_count": _parse_int(row["涨停数量"]),
                "turnover_amount_billion": _parse_number(row["成交额"]),
                "turnover_change_pct": _parse_number(row["放量幅度"]),
                "leading_stock": row["领涨股票"],
                "leading_stock_change_pct": _parse_number(row["领涨股票涨幅"]),
            }
        )

    return {
        "trade_date": datetime.now().strftime("%Y-%m-%d"),
        "sectors": sectors,
    }


def load_manual_data(data_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events = load_manual_news(data_dir / "manual_news.csv")
    announcements = load_manual_announcements(data_dir / "manual_announcements.csv")
    market_data = load_manual_market(data_dir / "manual_market.csv")
    if not events and not announcements and not market_data.get("sectors"):
        market_data["empty_message"] = "暂无手动导入数据"
    return events, announcements, market_data

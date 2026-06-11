"""Extract A-share stock names and codes from news text and local mapping."""

from __future__ import annotations

import csv
import re
from pathlib import Path


STOCK_CODE_PATTERN = r"(?:600|601|603|605|688|000|001|002|003|300|301)\d{3}"
STOCK_CODE_RE = re.compile(rf"\b{STOCK_CODE_PATTERN}\b")
NAME_RE = r"[\u4e00-\u9fa5A-Za-z]{2,12}"
PLACEHOLDER_MARKERS = ["某某"]
INVALID_NAME_PARTS = [
    "今日",
    "概念股",
    "上市公司",
    "产业链",
    "板块",
    "股票",
    "股票简称",
    "证券时报",
    "东方财富",
    "财联社",
    "同花顺",
    "巨潮资讯",
    "中国证券报",
    "第一财经",
    "证券日报",
    "龙虎榜",
    "资金",
    "异动",
]


def load_stock_code_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        return {
            str(row.get("股票名称", "")).strip(): str(row.get("股票代码", "")).strip()
            for row in csv.DictReader(file)
            if str(row.get("股票名称", "")).strip()
        }


def extract_stocks_from_text(text: str, code_map: dict[str, str] | None = None) -> list[dict[str, str]]:
    mapping = code_map or {}
    value = str(text or "")
    results: list[dict[str, str]] = []

    patterns = [
        rf"({NAME_RE})[（(]\s*({STOCK_CODE_PATTERN})\s*[）)]",
        rf"({NAME_RE})\s+({STOCK_CODE_PATTERN})",
        rf"({STOCK_CODE_PATTERN})\s+({NAME_RE})",
        rf"({NAME_RE})[:：]?\s*代码\s*({STOCK_CODE_PATTERN})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            first, second = match.group(1), match.group(2)
            if STOCK_CODE_RE.fullmatch(first):
                code, name = first, second
            else:
                name, code = first, second
            _append_stock(results, name, code)

    for name, code in mapping.items():
        if name and name in value:
            _append_stock(results, name, code)

    return _dedupe(results)


def stock_from_leading_name(value: str, code_map: dict[str, str] | None = None) -> dict[str, str] | None:
    name = clean_stock_name(value)
    if not name or is_placeholder_name(name):
        return None
    mapping = code_map or {}
    return {"股票名称": name, "股票代码": mapping.get(name, ""), "候选类型": "个股" if mapping.get(name, "") else "个股待补代码"}


def clean_stock_name(value: str) -> str:
    text = str(value or "").strip()
    text = STOCK_CODE_RE.sub("", text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"^(龙头|核心股|人气股|领涨股|今日|A股)", "", text)
    text = re.sub(r"(等|股份有限公司|有限公司)$", "", text)
    text = re.split(r"[,，、/；;| ]+", text)[0].strip()
    return text


def is_placeholder_name(value: str) -> bool:
    return any(marker in str(value or "") for marker in PLACEHOLDER_MARKERS)


def _append_stock(results: list[dict[str, str]], name: str, code: str) -> None:
    clean_name = clean_stock_name(name)
    clean_code = code.strip()
    if not _looks_like_stock_name(clean_name):
        return
    candidate_type = "个股" if clean_code else "个股待补代码"
    results.append({"股票名称": clean_name, "股票代码": clean_code, "候选类型": candidate_type})


def _looks_like_stock_name(value: str) -> bool:
    if not value or len(value) < 2 or len(value) > 10:
        return False
    if is_placeholder_name(value):
        return False
    return not any(part in value for part in INVALID_NAME_PARTS)


def _dedupe(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("股票名称", ""), row.get("股票代码", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result

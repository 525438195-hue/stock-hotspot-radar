"""Build search query matrices from hotspot base keywords."""

from __future__ import annotations


class QueryBuilder:
    DEFAULT_SUFFIXES = [
        "A股 今日",
        "概念股",
        "板块 涨停",
        "上市公司",
        "产业链 A股",
        "政策 A股",
        "交易所公告",
        "巨潮资讯 公告",
        "东方财富",
        "财联社",
    ]

    DEFAULT_SYNONYMS = {
        "低空经济": ["无人机", "通航", "eVTOL", "城市空中交通"],
        "AI算力": ["服务器", "光模块", "液冷", "数据中心", "英伟达链"],
        "机器人": ["人形机器人", "减速器", "伺服电机", "机器视觉"],
    }

    def __init__(
        self,
        base_keywords: list[str],
        synonyms: dict[str, list[str]] | None = None,
        suffixes: list[str] | None = None,
    ) -> None:
        self.base_keywords = [keyword.strip() for keyword in base_keywords if keyword.strip()]
        self.synonyms = {**self.DEFAULT_SYNONYMS, **(synonyms or {})}
        self.suffixes = suffixes or self.DEFAULT_SUFFIXES

    def build(self) -> list[str]:
        queries: list[str] = []
        for suffix in self.suffixes:
            for keyword in self.base_keywords:
                queries.append(f"{keyword} {suffix}")
        for keyword in self.base_keywords:
            for synonym in self.synonyms.get(keyword, []):
                for suffix in self.suffixes:
                    queries.append(f"{synonym} {suffix}")
        return _dedupe_preserve_order(queries)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

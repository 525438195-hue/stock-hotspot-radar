"""RSS news fetcher."""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from source_config import enabled_sources, normalize_source_type

from .base import BaseFetcher


class RssNewsFetcher(BaseFetcher):
    source_type = "financial_news"

    def __init__(self, rss_sources: list[dict[str, Any]], max_items_per_source: int = 20) -> None:
        self.name = "RSS财经新闻"
        self.rss_sources = enabled_sources(rss_sources)
        self.max_items_per_source = max_items_per_source

    def fetch(self) -> list[dict[str, Any]]:
        if not self.rss_sources:
            raise ValueError("config/sources.yaml 未配置 rss_sources")

        try:
            import feedparser  # type: ignore
            import requests  # type: ignore
        except Exception as exc:
            raise RuntimeError("缺少 feedparser 或 requests，无法读取 RSS") from exc

        raw_items: list[dict[str, Any]] = []
        for source in self.rss_sources:
            url = str(source.get("url", "")).strip()
            if not url:
                continue
            response = requests.get(url, timeout=10, headers={"User-Agent": "stock-hotspot-radar/0.3"})
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if getattr(parsed, "bozo", False):
                bozo_exception = getattr(parsed, "bozo_exception", "")
                if bozo_exception:
                    raise RuntimeError(f"{source.get('name', url)} RSS 解析失败：{bozo_exception}")
            for entry in list(parsed.entries)[: self.max_items_per_source]:
                raw_items.append({"source_config": source, "entry": entry})
        return raw_items

    def normalize(self, raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index, item in enumerate(raw_data, start=1):
            source = item["source_config"]
            entry = item["entry"]
            title = str(getattr(entry, "title", "")).strip()
            if not title:
                continue
            summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
            published = _entry_time(entry)
            events.append(
                {
                    "event_id": f"RSS_{index:04d}",
                    "title": title,
                    "content": summary,
                    "topic_hint": "",
                    "tickers": [],
                    "source": str(source.get("name", "RSS财经新闻")),
                    "source_type": normalize_source_type(source.get("source_type"), self.source_type),
                    "publish_time": published,
                    "url": str(getattr(entry, "link", "") or source.get("url", "")),
                    "duplicate_count": 1,
                }
            )
        return events


def _entry_time(entry: Any) -> str:
    for attr in ["published", "updated", "created"]:
        value = str(getattr(entry, attr, "") or "").strip()
        if not value:
            continue
        try:
            return parsedate_to_datetime(value).isoformat()
        except Exception:
            return value
    return datetime.now().astimezone().isoformat()

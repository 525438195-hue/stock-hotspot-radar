"""Announcement fetcher scaffold.

This deliberately avoids login simulation, anti-bot bypassing, or scraping behind
access controls. It attempts only public, configurable URLs and lets auto mode
fall back to manual CSV data when unavailable.
"""

from __future__ import annotations

from typing import Any

from .base import BaseFetcher


class AnnouncementFetcher(BaseFetcher):
    name = "公开公告源"
    source_type = "official_announcement"

    def __init__(self, announcement_sources: list[dict[str, Any]] | None = None) -> None:
        self.announcement_sources = announcement_sources or []

    def fetch(self) -> list[dict[str, Any]]:
        if not self.announcement_sources:
            raise ValueError("暂未配置公开公告源")

        try:
            import requests  # type: ignore
            from bs4 import BeautifulSoup  # type: ignore
        except Exception as exc:
            raise RuntimeError("缺少 requests 或 beautifulsoup4，无法读取公开公告源") from exc

        items: list[dict[str, Any]] = []
        for source in self.announcement_sources:
            source_config = source.get("config", source)
            url = str(source_config.get("url", "")).strip()
            if not url:
                continue
            response = requests.get(url, timeout=10, headers={"User-Agent": "stock-hotspot-radar/0.3"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            title = (soup.title.string or "").strip() if soup.title else ""
            if title:
                items.append({"source": source, "title": title, "url": url, "content": soup.get_text(" ", strip=True)[:500]})
        return items

    def normalize(self, raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        announcements: list[dict[str, Any]] = []
        for index, item in enumerate(raw_data, start=1):
            announcements.append(
                {
                    "announcement_id": f"AUTO_ANN_{index:04d}",
                    "company": str(item["source"].get("source_name", "公开公告源")),
                    "ticker": str(item["source"].get("config", {}).get("ticker", "")),
                    "title": item["title"],
                    "content": item["content"],
                    "announcement_type": str(item["source"].get("config", {}).get("announcement_type", "other")),
                    "source": str(item["source"].get("source_name", "公开公告源")),
                    "source_type": str(item["source"].get("source_type", self.source_type)),
                    "publish_time": "",
                    "url": item["url"],
                }
            )
        return announcements

"""Configured social sentiment inputs.

The current implementation only supports manually maintained rumor CSV files.
It does not scrape social platforms, simulate login, or bypass anti-bot controls.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import BaseFetcher
from text_normalizer import normalize_text


RUMOR_HEADERS = ["标题", "正文", "来源", "发布时间", "原始链接", "相关关键词"]


class SocialSentimentFetcher(BaseFetcher):
    name = "社媒情绪源"
    source_type = "social_sentiment"

    def __init__(self, social_sources: list[dict[str, Any]], project_root: Path) -> None:
        self.social_sources = social_sources
        self.project_root = project_root

    def fetch(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source in self.social_sources:
            if source.get("key") == "manual_rumors":
                rows.extend(self._read_manual_rumors(source))
        return rows

    def normalize(self, raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index, row in enumerate(raw_data, start=1):
            events.append(
                {
                    "event_id": f"SOCIAL_{index:04d}",
                    "title": row["标题"],
                    "content": row["正文"],
                    "topic_hint": _topic_hint(row["相关关键词"], row["标题"], row["正文"]),
                    "tickers": [],
                    "source": row["来源"] or "手动社媒情绪",
                    "source_type": row.get("来源类型") or self.source_type,
                    "publish_time": _normalize_publish_time(row["发布时间"]),
                    "url": row["原始链接"],
                    "duplicate_count": 1,
                    "candidate_only": True,
                    "sentiment_only": True,
                }
            )
        return events

    def _read_manual_rumors(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        source_config = config.get("config", config)
        path = self._resolve_path(str(source_config.get("file", "data/manual_rumors.csv")))
        if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
            return []

        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                return []
            missing = [header for header in RUMOR_HEADERS if header not in reader.fieldnames]
            if missing:
                raise ValueError(f"{path.name} 缺少字段：{', '.join(missing)}")

            rows = []
            for row in reader:
                normalized = {key: normalize_text(str(row.get(key, "") or "").strip()) for key in RUMOR_HEADERS}
                if any(normalized.values()):
                    normalized["来源类型"] = str(config.get("source_type", self.source_type))
                    rows.append(normalized)
            return rows

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.project_root / path


def _normalize_publish_time(value: str) -> str:
    text = value.strip()
    if not text:
        return datetime.now().astimezone().isoformat()
    for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"]:
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


def _topic_hint(keywords: str, title: str, content: str) -> str:
    for keyword in [part.strip() for part in keywords.replace("，", ",").split(",") if part.strip()]:
        return keyword
    text = f"{title}\n{content}"
    for topic in ["低空经济", "AI算力", "机器人", "半导体", "军工", "新能源", "医药", "数据要素"]:
        if topic in text:
            return topic
    return ""

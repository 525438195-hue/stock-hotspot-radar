"""Core data models for the hotspot radar pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FactStatus = Literal["confirmed_fact", "inference", "unverified_rumor", "market_reaction"]


@dataclass(frozen=True)
class HotspotEvent:
    event_id: str
    title: str
    content: str
    source: str
    source_type: str
    publish_time: str
    url: str
    topic_hint: str = ""
    tickers: list[str] = field(default_factory=list)
    duplicate_count: int = 1
    related_event_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HotspotEvent":
        return cls(
            event_id=str(data["event_id"]),
            title=str(data["title"]),
            content=str(data.get("content", "")),
            source=str(data["source"]),
            source_type=str(data["source_type"]),
            publish_time=str(data["publish_time"]),
            url=str(data["url"]),
            topic_hint=str(data.get("topic_hint", "")),
            tickers=list(data.get("tickers", [])),
            duplicate_count=int(data.get("duplicate_count", 1)),
            related_event_ids=list(data.get("related_event_ids", [data["event_id"]])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "source_type": self.source_type,
            "publish_time": self.publish_time,
            "url": self.url,
            "topic_hint": self.topic_hint,
            "tickers": self.tickers,
            "duplicate_count": self.duplicate_count,
            "related_event_ids": self.related_event_ids,
        }


@dataclass(frozen=True)
class Announcement:
    announcement_id: str
    company: str
    ticker: str
    title: str
    content: str
    announcement_type: str
    source: str
    source_type: str
    publish_time: str
    url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Announcement":
        return cls(
            announcement_id=str(data["announcement_id"]),
            company=str(data["company"]),
            ticker=str(data["ticker"]),
            title=str(data["title"]),
            content=str(data.get("content", "")),
            announcement_type=str(data["announcement_type"]),
            source=str(data["source"]),
            source_type=str(data["source_type"]),
            publish_time=str(data["publish_time"]),
            url=str(data["url"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "announcement_id": self.announcement_id,
            "company": self.company,
            "ticker": self.ticker,
            "title": self.title,
            "content": self.content,
            "announcement_type": self.announcement_type,
            "source": self.source,
            "source_type": self.source_type,
            "publish_time": self.publish_time,
            "url": self.url,
        }


@dataclass(frozen=True)
class RiskFlag:
    risk_type: str
    reason: str
    severity: Literal["low", "medium", "high"] = "medium"

    def to_dict(self) -> dict[str, str]:
        return {
            "risk_type": self.risk_type,
            "reason": self.reason,
            "severity": self.severity,
        }

"""Base fetcher with safe failure handling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseFetcher(ABC):
    name: str = "未命名数据源"
    source_type: str = "unknown"

    @abstractmethod
    def fetch(self) -> Any:
        """Fetch raw data from a source."""

    @abstractmethod
    def normalize(self, raw_data: Any) -> list[dict[str, Any]] | dict[str, Any]:
        """Normalize raw data into pipeline dictionaries."""

    def safe_fetch(self) -> dict[str, Any]:
        try:
            raw_data = self.fetch()
            items = self.normalize(raw_data)
            count = len(items.get("sectors", [])) if isinstance(items, dict) else len(items)
            return {
                "success": True,
                "source": self.name,
                "items": items,
                "warning": "" if count else "数据源返回为空",
            }
        except Exception as exc:
            return {
                "success": False,
                "source": self.name,
                "items": [],
                "warning": str(exc),
            }


from __future__ import annotations

import csv
from pathlib import Path

from watchlist_monitor import run_watchlist_monitor


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _disable_network(monkeypatch) -> None:
    monkeypatch.setattr("watchlist_monitor.runtime_secret", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        "watchlist_monitor.load_source_config",
        lambda _root: {
            "tavily_search": {
                "include_domains": [],
                "exclude_domains": [],
                "search_depth": "basic",
                "country": "china",
            }
        },
    )


def test_watchlist_monitor_creates_template_and_reviews_without_api_key(tmp_path, monkeypatch):
    _disable_network(monkeypatch)

    metrics = run_watchlist_monitor(tmp_path)

    assert (tmp_path / "data" / "watchlist.csv").exists()
    assert (tmp_path / "outputs" / "watchlist_news.csv").exists()
    assert (tmp_path / "outputs" / "watchlist_review.csv").exists()
    assert metrics["watchlist_stock_count"] == 3
    assert metrics["watchlist_news_count"] == 0

    review_rows = _read_rows(tmp_path / "outputs" / "watchlist_review.csv")
    assert len(review_rows) == 3
    assert {row["情报状态"] for row in review_rows} == {"暂无新消息"}
    assert {row["规则观察建议"] for row in review_rows} == {"信息不足"}


def test_watchlist_monitor_handles_empty_watchlist(tmp_path, monkeypatch):
    _disable_network(monkeypatch)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "watchlist.csv").write_text(
        "股票名称,股票代码,所属题材,关注级别,持仓状态,成本价,备注\n",
        encoding="utf-8-sig",
    )

    metrics = run_watchlist_monitor(tmp_path)

    assert metrics["watchlist_stock_count"] == 0
    assert _read_rows(tmp_path / "outputs" / "watchlist_news.csv") == []
    assert _read_rows(tmp_path / "outputs" / "watchlist_review.csv") == []

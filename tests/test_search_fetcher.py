import csv
import json
from pathlib import Path

from fetchers.search_fetcher import SearchFetcher
from source_config import load_source_config


PROJECT_SOURCES_CONFIG = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"


def test_search_fetcher_skips_missing_api_keys_without_error(monkeypatch, tmp_path):
    for key in ["TAVILY_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CSE_ID", "NEWSAPI_KEY", "NEWS_API_KEY"]:
        monkeypatch.delenv(key, raising=False)

    result = SearchFetcher(
        queries=["AI算力 政策"],
        sources_config=load_source_config(tmp_path, PROJECT_SOURCES_CONFIG),
        project_root=tmp_path,
    ).safe_fetch()

    assert result["success"] is False
    assert result["items"] == []
    assert result["coverage"]["searched_queries_count"] == 1
    assert "Tavily Search API" in result["coverage"]["skipped_sources"]
    assert "Google Programmable Search JSON API" in result["coverage"]["skipped_sources"]
    assert "NewsAPI" in result["coverage"]["skipped_sources"]
    assert "东方财富财经" in result["coverage"]["skipped_sources"]
    assert "财联社" in result["coverage"]["skipped_sources"]
    assert "证券时报" in result["coverage"]["skipped_sources"]
    tavily_status = next(status for status in result["source_status"] if status["source"] == "Tavily Search API")
    assert tavily_status["warning"] == "跳过 Tavily：未配置 TAVILY_API_KEY"
    assert (tmp_path / "outputs" / "search_results_raw.csv").exists()
    assert (tmp_path / "outputs" / "search_results_deduped.csv").exists()


def test_search_fetcher_writes_tavily_results_for_llm_input(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / ".env").write_text("TAVILY_API_KEY=test-key\n", encoding="utf-8")
    (config_dir / "sources.yaml").write_text(
        """
search_sources:
  tavily:
    enabled: true
    source_type: 搜索API
    priority: 3
    api_key_env: TAVILY_API_KEY
rss_sources: []
official_sources: []
market_sources: {}
social_sources: {}
search:
  max_results_per_query: 2
""",
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "AI算力产业链更新",
                        "content": "服务器和光模块方向出现新增消息。",
                        "url": "https://example.com/news/1",
                        "published_date": "2026-06-09T09:30:00+08:00",
                    }
                ]
            }

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def post(self, url, headers, json, timeout):
            assert self.trust_env is False
            return fake_post(url, headers, json, timeout)

    def fake_post(url, headers, json, timeout):
        assert url == "https://api.tavily.com/search"
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"
        assert json["max_results"] == 2
        assert json["query"] == "AI算力 A股"
        assert json["search_depth"] == "basic"
        assert "api_key" not in json
        return FakeResponse()

    import requests

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(requests, "Session", FakeSession)

    result = SearchFetcher(
        queries=["AI算力 A股"],
        sources_config=load_source_config(tmp_path),
        project_root=tmp_path,
    ).safe_fetch()

    assert result["success"] is True
    raw_path = tmp_path / "outputs" / "search_results_raw.csv"
    deduped_path = tmp_path / "outputs" / "search_results_deduped.csv"
    with raw_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    with deduped_path.open(encoding="utf-8-sig", newline="") as file:
        deduped_rows = list(csv.DictReader(file))

    assert rows[0]["标题"] == "AI算力产业链更新"
    assert rows[0]["查询词"] == "AI算力 A股"
    assert rows[0]["是否来自Tavily"] == "是"
    assert rows[0]["是否来自RSS"] == "否"
    assert deduped_rows == rows

    debug = json.loads((tmp_path / "outputs" / "tavily_debug.json").read_text(encoding="utf-8"))
    assert debug["entries"][0]["request"]["headers"]["Authorization"] == "Bearer <redacted>"
    assert "api_key" not in debug["entries"][0]["request"]["body"]

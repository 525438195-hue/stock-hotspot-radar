from coverage_report import build_coverage_report


def test_coverage_report_counts_sources_and_domains():
    report = build_coverage_report(
        queries=["AI算力 政策", "机器人 公告"],
        source_status=[
            {"source": "Tavily Search API", "status": "success"},
            {"source": "NewsAPI", "status": "failed"},
            {"source": "Google Programmable Search JSON API", "status": "skipped"},
        ],
        raw_results=[
            {"url": "https://example.com/a", "source_type": "financial_news"},
            {"url": "https://example.com/a", "source_type": "financial_news"},
            {"url": "https://news.example.cn/b", "source_type": "industry_news"},
        ],
        deduped_results=[
            {"url": "https://example.com/a", "source_type": "financial_news"},
            {"url": "https://news.example.cn/b", "source_type": "industry_news"},
        ],
        warnings=["NewsAPI 超时"],
    )

    assert report["searched_queries_count"] == 2
    assert report["successful_sources"] == ["Tavily Search API"]
    assert report["failed_sources"] == ["NewsAPI"]
    assert report["skipped_sources"] == ["Google Programmable Search JSON API"]
    assert report["raw_results_count"] == 3
    assert report["deduped_results_count"] == 2
    assert report["unique_domains_count"] == 2
    assert report["source_type_coverage"]["financial_news"] == 1
    assert report["warnings"] == ["NewsAPI 超时"]

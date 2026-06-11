from source_config import configured_sources, enabled_sources, load_env, load_source_config, normalize_source_type, secret


def test_chinese_source_types_are_normalized():
    assert normalize_source_type("公司公告") == "official_announcement"
    assert normalize_source_type("交易所公告") == "exchange_announcement"
    assert normalize_source_type("搜索API") == "search_api"
    assert normalize_source_type("社媒情绪") == "social_sentiment"


def test_enabled_sources_are_sorted_by_priority():
    sources = [
        {"name": "社媒", "enabled": True, "priority": 5},
        {"name": "公告", "enabled": True, "priority": 1},
        {"name": "停用", "enabled": False, "priority": 0},
    ]

    assert [source["name"] for source in enabled_sources(sources)] == ["公告", "社媒"]


def test_secret_reads_configured_env_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "token-123")

    assert secret({}, "TAVILY_API_KEY") == "token-123"


def test_load_env_reads_dotenv_file(tmp_path):
    (tmp_path / ".env").write_text("TAVILY_API_KEY=local-token\nNEWSAPI_KEY=\n", encoding="utf-8")

    values = load_env(tmp_path)

    assert values["TAVILY_API_KEY"] == "local-token"
    assert values["NEWSAPI_KEY"] == ""


def test_load_source_config_builds_sorted_source_status(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / ".env").write_text("TAVILY_API_KEY=\n", encoding="utf-8")
    (config_dir / "sources.yaml").write_text(
        """
search_sources:
  tavily:
    enabled: true
    source_type: 搜索API
    priority: 3
    api_key_env: TAVILY_API_KEY
rss_sources:
  - name: 测试RSS
    enabled: true
    source_type: 财经媒体
    priority: 2
    url: ""
official_sources:
  - name: 测试公告
    enabled: true
    source_type: 公司公告
    priority: 1
    mode: placeholder
market_sources:
  akshare:
    enabled: true
    source_type: 行情数据
    priority: 2
social_sources: {}
""",
        encoding="utf-8",
    )

    config = load_source_config(tmp_path)
    statuses = config["_source_status"]

    assert [item["source_name"] for item in statuses][:2] == ["测试公告", "A股板块行情"]
    tavily = next(item for item in statuses if item["source_name"] == "Tavily Search API")
    assert tavily["status"] == "skipped"
    assert tavily["reason"] == "跳过 Tavily：未配置 TAVILY_API_KEY"
    runnable = configured_sources(config, "market_sources", runnable_only=True)
    assert runnable[0]["source_name"] == "A股板块行情"

"""Central source configuration registry.

This module is the single place that reads config/sources.yaml, reads .env,
sorts sources by priority, and explains why a source is ready or skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from secrets_manager import load_dotenv_values, runtime_secret

SOURCE_TYPE_ALIASES = {
    "政策文件": "government_policy",
    "政府政策": "government_policy",
    "公司公告": "official_announcement",
    "官方公告": "official_announcement",
    "交易所公告": "exchange_announcement",
    "交易所披露": "exchange_announcement",
    "财经媒体": "financial_news",
    "财经新闻": "financial_news",
    "行业媒体": "industry_news",
    "行业新闻": "industry_news",
    "海外新闻": "overseas_news",
    "搜索API": "search_api",
    "社媒情绪": "social_sentiment",
    "社媒": "social_sentiment",
    "股吧": "social_sentiment",
    "截图": "social_sentiment",
    "行情数据": "market_data",
    "市场数据": "market_data",
    "未知来源": "unknown",
    "未知": "unknown",
}

SEARCH_SOURCE_NAMES = {
    "tavily": "Tavily Search API",
    "google_cse": "Google Programmable Search JSON API",
    "newsapi": "NewsAPI",
}

MARKET_SOURCE_NAMES = {
    "akshare": "A股板块行情",
}

SOCIAL_SOURCE_NAMES = {
    "manual_rumors": "手动社媒情绪",
    "douyin_search": "抖音间接搜索",
}


def load_source_config(project_root: Path, path: Path | None = None) -> dict[str, Any]:
    config_path = path or project_root / "config" / "sources.yaml"
    config = read_sources_yaml(config_path)
    env_values = load_env(project_root)
    env_values["_project_root"] = str(project_root)
    source_records = build_source_records(config, env_values)
    config["_project_root"] = str(project_root)
    config["_env"] = env_values
    config["_source_records"] = source_records
    config["_source_status"] = [status_from_record(record) for record in source_records]
    return config


def read_sources_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
    except Exception:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return loaded


def normalize_source_type(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return SOURCE_TYPE_ALIASES.get(text, text)


def enabled(source: dict[str, Any]) -> bool:
    return bool(source.get("enabled", True))


def priority(source: dict[str, Any], default: int = 99) -> int:
    try:
        return int(source.get("priority", default))
    except (TypeError, ValueError):
        return default


def enabled_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted([source for source in sources if enabled(source)], key=priority)


def configured_sources(
    sources_config: dict[str, Any],
    group: str | None = None,
    *,
    runnable_only: bool = False,
) -> list[dict[str, Any]]:
    records = list(sources_config.get("_source_records") or build_source_records(sources_config, sources_config.get("_env", {})))
    if group:
        records = [record for record in records if record["group"] == group]
    if runnable_only:
        records = [record for record in records if record["status"] == "success"]
    return sorted(records, key=lambda record: (record["priority"], record["source_name"]))


def source_statuses(sources_config: dict[str, Any]) -> list[dict[str, Any]]:
    records = configured_sources(sources_config)
    return [status_from_record(record) for record in records]


def status_from_record(
    record: dict[str, Any],
    *,
    status: str | None = None,
    reason: str | None = None,
    item_count: int | None = None,
) -> dict[str, Any]:
    actual_status = status or str(record.get("status", "skipped"))
    actual_reason = reason if reason is not None else str(record.get("reason", ""))
    row = {
        "source_name": record.get("source_name", ""),
        "source_type": record.get("source_type", "unknown"),
        "enabled": bool(record.get("enabled", False)),
        "priority": int(record.get("priority", 99)),
        "status": actual_status,
        "reason": actual_reason,
        "source": record.get("source_name", ""),
        "success": actual_status == "success",
        "warning": actual_reason,
    }
    if item_count is not None:
        row["item_count"] = int(item_count)
    return row


def build_source_records(config: dict[str, Any], env_values: dict[str, str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_search_records(config.get("search_sources", {}), env_values))
    records.extend(_list_records(config.get("rss_sources", []), "rss_sources", default_name="RSS 新闻源", requires_url=True))
    records.extend(_official_records(config.get("official_sources", [])))
    records.extend(_mapping_records(config.get("market_sources", {}), "market_sources", MARKET_SOURCE_NAMES))
    records.extend(_social_records(config.get("social_sources", {})))
    return sorted(records, key=lambda record: (record["priority"], record["source_name"]))


def load_env(project_root: Path) -> dict[str, str]:
    return load_dotenv_values(project_root)


def secret(env_values: dict[str, str], env_key: object) -> str:
    key = str(env_key or "").strip()
    if not key:
        return ""
    project_root = Path(env_values.get("_project_root", ".")).resolve()
    return runtime_secret(project_root, key, env_values)


def _search_records(config: dict[str, Any], env_values: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for key, source in config.items():
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("name") or SEARCH_SOURCE_NAMES.get(key, key))
        api_key = secret(env_values, source.get("api_key_env"))
        cse_id = secret(env_values, source.get("cse_id_env"))
        status, reason = _base_status(source)
        if status == "success" and source.get("api_key_env") and not api_key:
            status, reason = "skipped", _missing_api_key_reason(key, source.get("api_key_env"))
        if status == "success" and source.get("cse_id_env") and not cse_id:
            status, reason = "skipped", "跳过：未配置 Google CSE ID"
        records.append(
            _record(
                group="search_sources",
                key=key,
                source_name=source_name,
                source=source,
                status=status,
                reason=reason,
                runtime={"api_key": api_key, "cse_id": cse_id},
            )
        )
    return records


def _list_records(
    sources: list[dict[str, Any]],
    group: str,
    *,
    default_name: str,
    requires_url: bool,
) -> list[dict[str, Any]]:
    records = []
    for index, source in enumerate(sources, start=1):
        source_name = str(source.get("name") or f"{default_name}{index}")
        status, reason = _base_status(source)
        if status == "success" and requires_url and not str(source.get("url", "")).strip():
            status, reason = "skipped", "跳过 RSS 源：URL 为空" if group == "rss_sources" else "跳过：未配置 URL"
        records.append(_record(group=group, key=source_name, source_name=source_name, source=source, status=status, reason=reason))
    return records


def _official_records(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for index, source in enumerate(sources, start=1):
        source_name = str(source.get("name") or f"官方公告源{index}")
        status, reason = _base_status(source)
        if status == "success" and str(source.get("mode", "")).strip() == "placeholder":
            status, reason = "placeholder", "占位源，未接入真实接口"
        elif status == "success" and not str(source.get("url", "")).strip():
            status, reason = "skipped", "跳过：未配置 URL"
        records.append(
            _record(group="official_sources", key=source_name, source_name=source_name, source=source, status=status, reason=reason)
        )
    return records


def _mapping_records(
    sources: dict[str, dict[str, Any]],
    group: str,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    records = []
    for key, source in sources.items():
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("name") or names.get(key, key))
        status, reason = _base_status(source)
        records.append(_record(group=group, key=key, source_name=source_name, source=source, status=status, reason=reason))
    return records


def _social_records(sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for key, source in sources.items():
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("name") or SOCIAL_SOURCE_NAMES.get(key, key))
        status, reason = _base_status(source)
        if status == "success" and key == "manual_rumors" and not str(source.get("file", "")).strip():
            status, reason = "skipped", "跳过：未配置文件"
        elif status == "success" and key != "manual_rumors":
            status, reason = "skipped", "跳过：该社媒源暂未接入 fetcher"
        records.append(_record(group="social_sources", key=key, source_name=source_name, source=source, status=status, reason=reason))
    return records


def _base_status(source: dict[str, Any]) -> tuple[str, str]:
    if not enabled(source):
        return "skipped", "跳过：未启用"
    return "success", "已启用"


def _missing_api_key_reason(key: str, env_key: object) -> str:
    env_name = str(env_key or "API Key")
    if key == "tavily":
        return f"跳过 Tavily：未配置 {env_name}"
    return f"跳过：未配置 {env_name}"


def _record(
    *,
    group: str,
    key: str,
    source_name: str,
    source: dict[str, Any],
    status: str,
    reason: str,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "group": group,
        "key": key,
        "source_name": source_name,
        "source_type": normalize_source_type(source.get("source_type")),
        "enabled": enabled(source),
        "priority": priority(source),
        "status": status,
        "reason": reason,
        "config": dict(source),
        "runtime": dict(runtime or {}),
    }

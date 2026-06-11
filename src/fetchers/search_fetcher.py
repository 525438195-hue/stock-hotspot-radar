"""Multi-source search fetcher for candidate hotspot information."""

from __future__ import annotations

import hashlib
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from coverage_report import build_coverage_report
from result_quality_filter import annotate_results, retained_results
from secrets_manager import runtime_secret
from source_config import configured_sources, status_from_record
from text_normalizer import normalize_text_with_flag, normalizer_warning


SEARCH_RESULT_FIELDS = [
    "标题",
    "摘要",
    "来源",
    "来源类型",
    "原始链接",
    "域名",
    "查询词",
    "抓取时间",
    "是否来自Tavily",
    "是否来自RSS",
    "是否保留",
    "过滤原因",
    "是否简体化",
    "A股相关性分数",
]

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_DEBUG_FILE = "tavily_debug.json"


class SearchFetcher:
    name = "多源搜索覆盖"
    source_type = "search_api"

    def __init__(
        self,
        queries: list[str],
        sources_config: dict[str, Any],
        project_root: Path,
        deadline: float | None = None,
    ) -> None:
        self.queries = queries
        self.sources_config = sources_config
        self.project_root = project_root
        self.search_config = sources_config.get("search", {})
        self.search_sources = sorted(configured_sources(sources_config, "search_sources"), key=_search_source_order)
        self.tavily_config = dict(sources_config.get("tavily_search", {}))
        configured_limit = int(self.tavily_config.get("max_results_per_query", self.search_config.get("max_results_per_query", 3)))
        self.max_results_per_query = max(1, min(configured_limit, 3))
        max_queries = int(self.search_config.get("max_queries_per_run", 20))
        self.queries = self.queries[: max(1, max_queries)]
        self.deadline = deadline

    def safe_fetch(self) -> dict[str, Any]:
        raw_results: list[dict[str, Any]] = []
        source_status: list[dict[str, Any]] = []
        warnings: list[str] = []

        service_fetchers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "tavily": self._fetch_tavily,
            "google_cse": self._fetch_google,
            "newsapi": self._fetch_newsapi,
        }
        for source in self.search_sources:
            service_name = str(source.get("source_name", source.get("key", "")))
            _log(f"正在抓取：{service_name}")
            if source.get("status") == "skipped":
                reason = str(source.get("reason", "已跳过"))
                _log(f"失败：原因 {reason}，已跳过")
                source_status.append(status_from_record(source, item_count=0))
                warnings.append(reason)
                continue

            fetcher = service_fetchers.get(str(source.get("key", "")))
            if fetcher is None:
                source_status.append(status_from_record(source, status="skipped", reason="跳过：该搜索源暂未接入 fetcher", item_count=0))
                _log("失败：原因 该搜索源暂未接入 fetcher，已跳过")
                continue
            self._run_source(service_name, source, fetcher, raw_results, source_status, warnings)

        for source in configured_sources(self.sources_config, "rss_sources"):
            service_name = str(source.get("source_name", "RSS 新闻源"))
            _log(f"正在抓取：RSS 新闻源 - {service_name}")
            if source.get("status") == "skipped":
                reason = str(source.get("reason", "已跳过"))
                _log(f"失败：原因 {reason}，已跳过")
                source_status.append(status_from_record(source, item_count=0))
                warnings.append(reason)
                continue
            self._run_source(service_name, source, self._fetch_rss, raw_results, source_status, warnings)

        normalized_warning = normalizer_warning()
        if normalized_warning:
            warnings.append(normalized_warning)

        annotated_results = annotate_results(
            raw_results,
            include_domains=self._include_domains(),
            exclude_domains=self._exclude_domains(),
        )
        deduped_results = _dedupe_results(annotated_results)
        retained = retained_results(deduped_results)
        self._write_search_result_csvs(annotated_results, deduped_results)
        coverage = build_coverage_report(self.queries, source_status, annotated_results, deduped_results, warnings)
        coverage["retained_results_count"] = len(retained)
        coverage["filtered_results_count"] = len([item for item in deduped_results if item.get("keep") != "是"])
        events = [self._result_to_event(index, result) for index, result in enumerate(retained, start=1)]

        return {
            "success": bool(annotated_results),
            "source": self.name,
            "items": events,
            "warning": "" if annotated_results else _empty_warning(source_status),
            "coverage": coverage,
            "raw_results": annotated_results,
            "deduped_results": deduped_results,
            "source_status": source_status,
        }

    def _run_source(
        self,
        service_name: str,
        source: dict[str, Any],
        fetcher: Callable[[dict[str, Any]], dict[str, Any]],
        raw_results: list[dict[str, Any]],
        source_status: list[dict[str, Any]],
        warnings: list[str],
    ) -> None:
        if self._deadline_reached():
            reason = "超时：auto 流程已超过 90 秒，已切换 fallback"
            source_status.append(status_from_record(source, status="timeout", reason=reason, item_count=0))
            warnings.append(reason)
            _log("超时：已切换 fallback")
            return
        try:
            result = fetcher(source)
        except TimeoutError as exc:
            source_status.append(status_from_record(source, status="timeout", reason=str(exc), item_count=0))
            warnings.append(str(exc))
            _log("超时：已切换 fallback")
            return
        except Exception as exc:
            reason = _request_exception_reason(service_name, exc)
            source_status.append(status_from_record(source, status="failed", reason=reason, item_count=0))
            warnings.append(f"{service_name} 失败：{reason}")
            _log(f"失败：原因 {reason}，已跳过")
            return

        status = str(result.get("status", "success"))
        items = list(result.get("items", []))
        warning = str(result.get("warning", ""))
        reason = warning or ("读取成功" if status == "success" else "")
        if status == "success" and not items:
            reason = warning or "请求成功但返回 0 条，可能是关键词过窄、当天无相关结果或数据源未收录"
            warnings.append(f"{service_name}：{reason}")
        source_status.append(status_from_record(source, status=status, reason=reason, item_count=len(items)))
        if warning:
            warnings.append(warning)
        if status == "success":
            if items:
                _log(f"成功：获取 {len(items)} 条")
            else:
                _log(f"成功：获取 0 条，原因 {reason}")
        elif status == "timeout":
            _log("超时：已切换 fallback")
        else:
            _log(f"失败：原因 {reason or status}，已跳过")
        raw_results.extend(items)

    def _fetch_tavily(self, source: dict[str, Any]) -> dict[str, Any]:
        api_key = self._tavily_api_key(source)
        debug_entries: list[dict[str, Any]] = []
        if not api_key:
            self._write_tavily_debug(
                [
                    {
                        "status": "failed",
                        "reason": "API Key 缺失：未配置 TAVILY_API_KEY",
                        "response": None,
                    }
                ]
            )
            return {"status": "failed", "items": [], "warning": "API Key 缺失：未配置 TAVILY_API_KEY"}

        import requests  # type: ignore

        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for query in self.queries:
            if self._deadline_reached():
                warnings.append("超时：auto 流程已超过 90 秒，保留已获取的 Tavily 结果")
                break
            body = {
                "query": query,
                "max_results": self.max_results_per_query,
                "search_depth": str(self.tavily_config.get("search_depth", "basic")),
                "include_raw_content": False,
                "include_answer": False,
                "topic": "news",
                "country": str(self.tavily_config.get("country", "china")),
                "exclude_domains": self._exclude_domains(),
            }
            include_domains = self._include_domains()
            if include_domains:
                body["include_domains"] = include_domains
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            _log(f"正在抓取：Tavily 搜索 - {query}")
            try:
                timeout = self._request_timeout(15)
                response = _post_tavily_with_optional_retry(requests, headers, body, timeout)
            except requests.exceptions.Timeout as exc:  # type: ignore[attr-defined]
                reason = _tavily_exception_detail("Timeout", exc)
                warnings.append(f"{query}：{reason}")
                debug_entries.append(_tavily_debug_entry(query, body, "timeout", reason=reason))
                _log(f"Tavily 失败：{query}；异常类型 Timeout；状态码 无；返回正文 无")
                break
            except requests.exceptions.ProxyError as exc:  # type: ignore[attr-defined]
                reason = _tavily_exception_detail("ProxyError", exc)
                warnings.append(f"{query}：{reason}")
                debug_entries.append(_tavily_debug_entry(query, body, "failed", reason=reason))
                _log(f"Tavily 失败：{query}；异常类型 ProxyError；状态码 无；返回正文 无")
                continue
            except requests.exceptions.ConnectionError as exc:  # type: ignore[attr-defined]
                reason = _tavily_exception_detail("ConnectionError", exc)
                warnings.append(f"{query}：{reason}")
                debug_entries.append(_tavily_debug_entry(query, body, "failed", reason=reason))
                _log(f"Tavily 失败：{query}；异常类型 ConnectionError；状态码 无；返回正文 无")
                continue
            if response.status_code in {401, 403}:
                response_text = _short_response_text(response)
                reason = f"认证失败：请检查 TAVILY_API_KEY；状态码 {response.status_code}；返回正文 {response_text}"
                debug_entries.append(_tavily_debug_entry(query, body, "failed", response=response, reason=reason))
                self._write_tavily_debug(debug_entries)
                _log(f"Tavily 失败：{query}；异常类型 HTTPError；状态码 {response.status_code}；返回正文 {response_text}")
                return {"status": "failed", "items": items, "warning": reason}
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:  # type: ignore[attr-defined]
                response_text = _short_response_text(response)
                reason = f"HTTPError：状态码 {response.status_code}；返回正文 {response_text}"
                warnings.append(f"{query}：{reason}")
                debug_entries.append(_tavily_debug_entry(query, body, "failed", response=response, reason=reason))
                _log(f"Tavily 失败：{query}；异常类型 HTTPError；状态码 {response.status_code}；返回正文 {response_text}")
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                response_text = _short_response_text(response)
                reason = f"JSON 解析失败：{exc}；返回正文 {response_text}"
                warnings.append(f"{query}：{reason}")
                debug_entries.append(_tavily_debug_entry(query, body, "failed", response=response, reason=reason))
                _log(f"Tavily 失败：{query}；异常类型 JSONDecodeError；状态码 {response.status_code}；返回正文 {response_text}")
                continue
            results = payload.get("results", [])
            if not results and body.get("include_domains"):
                fallback_body = dict(body)
                fallback_body.pop("include_domains", None)
                try:
                    fallback_response = _post_tavily_with_optional_retry(requests, headers, fallback_body, self._request_timeout(15))
                    fallback_response.raise_for_status()
                    fallback_payload = fallback_response.json()
                    fallback_results = fallback_payload.get("results", [])
                    if isinstance(fallback_results, list) and fallback_results:
                        response = fallback_response
                        payload = fallback_payload
                        results = fallback_results
                        debug_entries.append(
                            _tavily_debug_entry(query, fallback_body, "fallback_success", response=fallback_response, payload=fallback_payload)
                        )
                except Exception as exc:
                    warnings.append(f"{query}：include_domains 结果为空后放宽域名失败：{exc}")
            debug_entries.append(_tavily_debug_entry(query, body, "success", response=response, payload=payload))
            if not isinstance(results, list):
                warnings.append(f"{query}：返回结果字段 results 不是列表")
                _log(f"Tavily 成功：{query} 获取 0 条；原因 results 字段不是列表")
                continue
            _log(f"Tavily 成功：{query} 获取 {len(results)} 条")
            for item in results:
                if not isinstance(item, dict):
                    continue
                items.append(
                    self._normalize_result(
                        title=item.get("title", ""),
                        snippet=item.get("content") or item.get("snippet", ""),
                        url=item.get("url", ""),
                        source=str(source.get("source_name", "Tavily Search API")),
                        source_type=str(source.get("source_type", "search_api")),
                        publish_time=item.get("published_date") or item.get("publishedAt", ""),
                        query=query,
                        is_tavily=True,
                        is_rss=False,
                    )
                )
        self._write_tavily_debug(debug_entries)
        warning = "；".join(warnings)
        if not items and not warning:
            warning = f"返回结果为空：Tavily 已请求 {len(self.queries)} 个查询词但未返回候选"
        status = "success" if items else ("timeout" if any("Timeout" in warning or "超时" in warning for warning in warnings) else "failed")
        return {"status": status, "items": items, "warning": warning}

    def _fetch_google(self, source: dict[str, Any]) -> dict[str, Any]:
        api_key = str(source.get("runtime", {}).get("api_key", ""))
        cse_id = str(source.get("runtime", {}).get("cse_id", ""))

        import requests  # type: ignore

        items: list[dict[str, Any]] = []
        for query in self.queries:
            timeout = self._request_timeout(15)
            response = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cse_id, "q": query, "num": min(self.max_results_per_query, 10)},
                timeout=timeout,
            )
            response.raise_for_status()
            for item in response.json().get("items", []):
                items.append(
                    self._normalize_result(
                        title=item.get("title", ""),
                        snippet=item.get("snippet", ""),
                        url=item.get("link", ""),
                        source=str(source.get("source_name", "Google Programmable Search")),
                        source_type=str(source.get("source_type", "search_api")),
                        publish_time="",
                        query=query,
                        is_tavily=False,
                        is_rss=False,
                    )
                )
        return {"status": "success", "items": items, "warning": ""}

    def _fetch_newsapi(self, source: dict[str, Any]) -> dict[str, Any]:
        api_key = str(source.get("runtime", {}).get("api_key", ""))

        import requests  # type: ignore

        items: list[dict[str, Any]] = []
        for query in self.queries:
            timeout = self._request_timeout(15)
            response = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "apiKey": api_key,
                    "q": query,
                    "language": "zh",
                    "pageSize": min(self.max_results_per_query, 100),
                    "sortBy": "publishedAt",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            for item in response.json().get("articles", []):
                source_name = (item.get("source") or {}).get("name") or "NewsAPI"
                items.append(
                    self._normalize_result(
                        title=item.get("title", ""),
                        snippet=item.get("description", ""),
                        url=item.get("url", ""),
                        source=f"NewsAPI:{source_name}",
                        source_type=str(source.get("source_type", "overseas_news")),
                        publish_time=item.get("publishedAt", ""),
                        query=query,
                        is_tavily=False,
                        is_rss=False,
                    )
                )
        return {"status": "success", "items": items, "warning": ""}

    def _fetch_rss(self, source: dict[str, Any]) -> dict[str, Any]:
        import feedparser  # type: ignore
        import requests  # type: ignore

        items: list[dict[str, Any]] = []
        source_config = source.get("config", {})
        url = str(source_config.get("url", "")).strip()
        response = requests.get(url, timeout=self._request_timeout(10), headers={"User-Agent": "stock-hotspot-radar/0.3"})
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        for entry in parsed.entries:
            title = str(getattr(entry, "title", "") or "")
            summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
            text = f"{title}\n{summary}"
            matched_query = next((query for query in self.queries if _query_matches(query, text)), "")
            if not matched_query:
                continue
            items.append(
                self._normalize_result(
                    title=title,
                    snippet=summary,
                    url=str(getattr(entry, "link", "") or url),
                    source=str(source.get("source_name", "RSS 新闻源")),
                    source_type=str(source.get("source_type", "financial_news")),
                    publish_time=str(getattr(entry, "published", "") or getattr(entry, "updated", "")),
                    query=matched_query,
                    is_tavily=False,
                    is_rss=True,
                )
            )
        warning = "" if items else f"返回结果为空：RSS 源 {source.get('source_name', 'RSS 新闻源')} 没有匹配关键词矩阵"
        return {"status": "success", "items": items, "warning": warning}

    def _include_domains(self) -> list[str]:
        values = self.tavily_config.get("include_domains", [])
        return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []

    def _exclude_domains(self) -> list[str]:
        values = self.tavily_config.get("exclude_domains", [])
        return [str(value).strip() for value in values if str(value).strip()] if isinstance(values, list) else []

    def _deadline_reached(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _request_timeout(self, max_seconds: int) -> float:
        if self.deadline is None:
            return float(max_seconds)
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError("超时：auto 流程已超过 90 秒，已切换 fallback")
        return min(float(max_seconds), remaining)

    def _normalize_result(
        self,
        title: str,
        snippet: str,
        url: str,
        source: str,
        source_type: str,
        publish_time: str,
        query: str,
        is_tavily: bool,
        is_rss: bool,
    ) -> dict[str, Any]:
        title_value, title_changed = normalize_text_with_flag(title)
        snippet_value, snippet_changed = normalize_text_with_flag(snippet)
        source_value, source_changed = normalize_text_with_flag(source)
        query_value, query_changed = normalize_text_with_flag(query)
        return {
            "title": title_value.strip(),
            "snippet": snippet_value.strip(),
            "url": str(url or "").strip(),
            "source": source_value.strip(),
            "source_type": source_type,
            "publish_time": str(publish_time or "").strip(),
            "query": query_value.strip(),
            "fetched_at": datetime.now().astimezone().isoformat(),
            "domain": _domain(url),
            "is_tavily": is_tavily,
            "is_rss": is_rss,
            "is_from_tavily": is_tavily,
            "is_from_rss": is_rss,
            "simplified": title_changed or snippet_changed or source_changed or query_changed,
        }

    def _result_to_event(self, index: int, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": f"SEARCH_{index:04d}",
            "title": result["title"],
            "content": result["snippet"],
            "topic_hint": _topic_from_query(str(result.get("query", ""))),
            "tickers": [],
            "source": result["source"],
            "source_type": result["source_type"],
            "publish_time": result.get("publish_time") or result.get("fetched_at", ""),
            "url": result["url"],
            "duplicate_count": 1,
            "search_query": result.get("query", ""),
            "candidate_only": True,
        }

    def _write_search_result_csvs(
        self,
        raw_results: list[dict[str, Any]],
        deduped_results: list[dict[str, Any]],
    ) -> None:
        output_dir = self.project_root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_search_results_csv(output_dir / "search_results_raw.csv", raw_results)
        _write_search_results_csv(output_dir / "search_results_deduped.csv", deduped_results)

    def _tavily_api_key(self, source: dict[str, Any]) -> str:
        api_key = runtime_secret(self.project_root, "TAVILY_API_KEY")
        if api_key:
            return api_key
        return str(source.get("runtime", {}).get("api_key", "")).strip()

    def _write_tavily_debug(self, entries: list[dict[str, Any]]) -> None:
        output_dir = self.project_root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now().astimezone().isoformat(),
            "note": "Tavily 调试文件已脱敏，不包含 API Key。",
            "request_count": len(entries),
            "entries": entries,
            "last_response": entries[-1] if entries else None,
        }
        (output_dir / TAVILY_DEBUG_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        if not result.get("title") or not result.get("url"):
            continue
        key = result.get("url") or hashlib.sha256(f"{result.get('title')}|{result.get('snippet')}".encode()).hexdigest()
        if key in seen:
            continue
        seen.add(str(key))
        deduped.append(result)
    return deduped


def _write_search_results_csv(path: Path, results: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SEARCH_RESULT_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "标题": result.get("title", ""),
                    "摘要": result.get("snippet", ""),
                    "来源": result.get("source", ""),
                    "来源类型": result.get("source_type", ""),
                    "原始链接": result.get("url", ""),
                    "域名": result.get("domain", ""),
                    "查询词": result.get("query", ""),
                    "抓取时间": result.get("fetched_at", ""),
                    "是否来自Tavily": "是" if result.get("is_from_tavily", result.get("is_tavily")) else "否",
                    "是否来自RSS": "是" if result.get("is_from_rss", result.get("is_rss")) else "否",
                    "是否保留": result.get("keep", ""),
                    "过滤原因": result.get("filter_reason", ""),
                    "是否简体化": "是" if result.get("simplified") else "否",
                    "A股相关性分数": result.get("a_share_score", ""),
                }
            )


def _post_tavily_without_env_proxy(requests_module: Any, headers: dict[str, str], body: dict[str, Any], timeout: float) -> Any:
    session = requests_module.Session()
    session.trust_env = False
    return session.post(TAVILY_URL, headers=headers, json=body, timeout=timeout)


def _post_tavily_with_optional_retry(requests_module: Any, headers: dict[str, str], body: dict[str, Any], timeout: float) -> Any:
    response = _post_tavily_without_env_proxy(requests_module, headers, body, timeout)
    if response.status_code != 400:
        return response
    retry_body = _minimal_tavily_body(body)
    if retry_body == body:
        return response
    retry_response = _post_tavily_without_env_proxy(requests_module, headers, retry_body, timeout)
    if retry_response.status_code < 500:
        return retry_response
    return response


def _minimal_tavily_body(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {"query", "max_results", "search_depth", "exclude_domains"}
    return {key: value for key, value in body.items() if key in allowed}


def _tavily_debug_entry(
    query: str,
    body: dict[str, Any],
    status: str,
    *,
    response: Any | None = None,
    payload: Any | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "query": query,
        "status": status,
        "request": {
            "url": TAVILY_URL,
            "method": "POST",
            "headers": {
                "Authorization": "Bearer <redacted>",
                "Content-Type": "application/json",
            },
            "body": dict(body),
        },
        "status_code": getattr(response, "status_code", None) if response is not None else None,
        "response_text_preview": _short_response_text(response) if response is not None else "",
        "response_json": payload,
        "reason": reason,
    }


def _short_response_text(response: Any, max_len: int = 500) -> str:
    if response is None:
        return "无"
    text = str(getattr(response, "text", "") or "")
    text = text.replace("\n", " ").replace("\r", " ").strip()
    return text[:max_len] if text else "无"


def _tavily_exception_detail(exception_type: str, exc: Exception) -> str:
    if exception_type == "Timeout":
        return f"Timeout：Tavily 请求超过 15 秒；异常类型 {exc.__class__.__name__}"
    if exception_type == "ProxyError":
        return f"ProxyError：系统代理或代理连接异常；异常类型 {exc.__class__.__name__}；详情 {exc}"
    if exception_type == "ConnectionError":
        return f"ConnectionError：网络连接失败；异常类型 {exc.__class__.__name__}；详情 {exc}"
    return f"{exception_type}：{exc}"


def _search_source_order(source: dict[str, Any]) -> tuple[int, int, str]:
    key = str(source.get("key", ""))
    source_rank = {"tavily": 0, "google_cse": 1, "newsapi": 2}.get(key, 9)
    return (source_rank, int(source.get("priority", 99)), str(source.get("source_name", "")))


def _domain(url: str) -> str:
    return urlparse(str(url)).netloc.lower().removeprefix("www.")


def _query_matches(query: str, text: str) -> bool:
    words = [word for word in query.split() if word]
    return any(word.lower() in text.lower() for word in words)


def _topic_from_query(query: str) -> str:
    for topic in ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "消费电子", "新能源", "医药", "证券"]:
        if topic in query:
            return topic
    return query.split()[0] if query else ""


def _empty_warning(source_status: list[dict[str, Any]]) -> str:
    runnable_statuses = {"success", "failed", "timeout"}
    runnable = [item for item in source_status if item.get("status") in runnable_statuses]
    if not runnable:
        return "当前没有可用联网新闻源，请配置 Tavily API Key 或 RSS URL。"

    reasons: list[str] = []
    for item in source_status:
        reason = str(item.get("reason") or item.get("warning") or "").strip()
        if reason:
            reasons.append(reason)
    unique_reasons = list(dict.fromkeys(reasons))
    if unique_reasons:
        return "所有搜索源均未返回候选结果；原因：" + "；".join(unique_reasons)
    return "所有搜索源均未返回候选结果；原因：数据源请求成功但返回 0 条"


def _request_exception_reason(service_name: str, exc: Exception) -> str:
    class_name = exc.__class__.__name__.lower()
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if "timeout" in class_name:
        return f"网络超时：{service_name} 请求超过限制时间"
    if status_code in {401, 403}:
        return "认证失败：请检查 API Key"
    if "connection" in class_name:
        return f"网络连接失败：{service_name} 无法连接"
    message = str(exc).strip()
    return message or f"{service_name} 请求失败"


def _log(message: str) -> None:
    print(message, flush=True)

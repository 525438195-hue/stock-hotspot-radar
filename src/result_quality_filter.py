"""Quality classification for online search results."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


MAINSTREAM_SOURCE_TERMS = [
    "东方财富",
    "财联社",
    "证券时报",
    "同花顺",
    "第一财经",
    "证券日报",
    "中国证券报",
    "巨潮资讯",
    "上交所",
    "深交所",
    "交易所公告",
]
MAINSTREAM_DOMAINS = {
    "eastmoney.com",
    "finance.eastmoney.com",
    "stock.eastmoney.com",
    "cls.cn",
    "stcn.com",
    "10jqka.com.cn",
    "yicai.com",
    "zqrb.cn",
    "cs.com.cn",
    "cnstock.com",
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
}
TRADING_TERMS = ["A股", "涨停", "概念股", "上市公司", "公告", "板块", "资金", "龙虎榜", "异动", "股票", "证券"]
TOPIC_TERMS = ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "新能源", "消费电子", "医药", "证券"]
LOW_QUALITY_TERMS = ["百科", "科普", "定义", "是什么", "入门", "指南", "Wikipedia", "Cloudflare"]
TAIWAN_TERMS = ["台湾", "台股", "台北", "柜买", "繁体"]
DEFAULT_EXCLUDED_DOMAINS = {
    "wikipedia.org",
    "zh.wikipedia.org",
    "cloudflare.com",
    "abb.com",
    "google.com",
    "youtube.com",
    "facebook.com",
    "reddit.com",
    "medium.com",
    "zhihu.com",
    "baike.baidu.com",
}
STOCK_CODE_RE = re.compile(r"\b(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}(?:\.(?:SZ|SH))?\b", re.I)


def score_search_result(
    result: dict[str, Any],
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    domain = _domain(str(result.get("url", ""))) or str(result.get("domain", ""))
    title = str(result.get("title", ""))
    snippet = str(result.get("snippet", ""))
    source = str(result.get("source", ""))
    query = str(result.get("query", ""))
    text = f"{title}\n{snippet}\n{source}\n{query}"
    score = 0
    reasons: list[str] = []
    blacklist = set(exclude_domains or []) or DEFAULT_EXCLUDED_DOMAINS
    include_set = set(include_domains or []) or MAINSTREAM_DOMAINS

    mainstream_source = any(term in text for term in MAINSTREAM_SOURCE_TERMS) or _domain_matches(domain, MAINSTREAM_DOMAINS)
    include_domain_source = _domain_matches(domain, include_set)
    has_trading_term = any(term in text for term in TRADING_TERMS)
    has_topic = any(term in text for term in TOPIC_TERMS)
    has_stock_code = bool(STOCK_CODE_RE.search(text))
    age_days = _age_days(str(result.get("publish_time", "")) or str(result.get("fetched_at", "")))

    if mainstream_source:
        score += 35
        reasons.append("主流财经或官方来源")
    elif include_domain_source:
        score += 25
        reasons.append("来源域名在已配置财经范围内")
    if has_trading_term:
        score += 30
        reasons.append("包含A股交易相关词")
    if has_stock_code:
        score += 20
        reasons.append("包含A股股票代码")
    if has_topic:
        score += 15
        reasons.append("包含热点题材关键词")
    if age_days is not None and age_days <= 3:
        score += 10
        reasons.append("近3天新闻")

    low_quality = _domain_matches(domain, blacklist) or _looks_like_low_quality(domain, text)
    if low_quality:
        score -= 50
        reasons.append("百科、海外企业官网、纯科普或排除域名")
    if any(term in text for term in TAIWAN_TERMS) and not any(term in text for term in ["A股", "沪深", "上市公司"]):
        score -= 20
        reasons.append("疑似台湾或海外内容且不涉及A股")
    if age_days is not None and age_days > 7:
        score -= 15
        reasons.append("超过7天旧闻，降权")
    elif age_days is not None and age_days > 3:
        score -= 5
        reasons.append("超过3天，轻度降权")

    score = max(0, min(100, int(score)))
    if low_quality or score < 25:
        result_type = "低质量剔除"
        keep = "否"
    elif mainstream_source or (has_trading_term and score >= 50):
        result_type = "高质量新闻"
        keep = "是"
    else:
        result_type = "题材参考"
        keep = "是"

    if result_type == "高质量新闻":
        filter_reason = "高质量新闻：" + "；".join(reasons or ["主流来源或A股交易相关"])
    elif result_type == "题材参考":
        filter_reason = "题材参考：" + "；".join(reasons or ["与题材相关但个股线索不足"])
    else:
        filter_reason = "低质量剔除：" + "；".join(reasons or ["A股相关性不足"])

    return {
        "a_share_score": score,
        "keep": keep,
        "filter_reason": filter_reason,
        "domain": domain,
        "quality_bucket": result_type,
        "result_type": result_type,
        "age_days": age_days if age_days is not None else "",
    }


def annotate_results(
    results: list[dict[str, Any]],
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    annotated = []
    for result in results:
        quality = score_search_result(result, include_domains=include_domains, exclude_domains=exclude_domains)
        row = dict(result)
        row.update(quality)
        annotated.append(row)
    return annotated


def retained_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in results if result.get("keep") == "是"]


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.")


def _domain_matches(domain: str, configured_domains: set[str]) -> bool:
    clean = domain.lower().removeprefix("www.")
    return any(clean == item or clean.endswith("." + item) for item in configured_domains)


def _looks_like_low_quality(domain: str, text: str) -> bool:
    if any(term.lower() in text.lower() for term in LOW_QUALITY_TERMS):
        return True
    if domain and not _domain_matches(domain, MAINSTREAM_DOMAINS):
        overseas_suffixes = (".com", ".org", ".net", ".io", ".ai")
        china_hints = (".cn", "eastmoney", "cls.cn", "stcn", "cnstock", "cs.com.cn", "sse", "szse", "cninfo", "10jqka")
        if domain.endswith(overseas_suffixes) and not any(hint in domain for hint in china_hints):
            return True
    return False


def _age_days(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    for candidate in [text, text.replace("Z", "+00:00")]:
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).days)
        except ValueError:
            continue
    match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        try:
            dt = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            return None
    return None

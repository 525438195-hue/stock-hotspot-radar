"""Quality scoring and filtering for online search results."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


A_SHARE_TERMS = ["A股", "股票", "上市公司", "涨停", "板块", "概念股", "公告", "证监会", "交易所"]
OFFICIAL_AND_MEDIA_TERMS = ["东方财富", "财联社", "证券时报", "上交所", "深交所", "巨潮资讯"]
TOPIC_TERMS = ["AI算力", "机器人", "低空经济", "半导体", "军工", "数据要素", "消费电子", "新能源", "医药", "证券"]
TRADING_TERMS = ["A股", "股票", "上市公司", "板块", "公告", "涨停", "概念股", "交易所", "证监会"]
DEFINITION_PATTERNS = [
    r"什么是.+",
    r".+是什么",
    r".+定义",
    r".+百科",
    r".+入门",
    r".+指南",
]
PURE_SCIENCE_TERMS = ["科普", "百科", "定义", "入门", "原理", "是什么"]
TAIWAN_TERMS = ["台湾", "台股", "台北", "柜买", "繁体"]
ENGLISH_RE = re.compile(r"^[\x00-\x7F\s\W]+$")
STOCK_CODE_RE = re.compile(r"\b(?:000|001|002|003|300|301|600|601|603|605|688)\d{3}(?:\.(?:SZ|SH))?\b", re.I)

DEFAULT_A_SHARE_DOMAINS = {
    "eastmoney.com",
    "finance.eastmoney.com",
    "stock.eastmoney.com",
    "cls.cn",
    "stcn.com",
    "cnstock.com",
    "cs.com.cn",
    "sse.com.cn",
    "szse.cn",
    "cninfo.com.cn",
    "10jqka.com.cn",
    "hexun.com",
    "jrj.com.cn",
    "yicai.com",
}

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


def score_search_result(
    result: dict[str, Any],
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    domain = _domain(str(result.get("url", ""))) or str(result.get("domain", ""))
    text = f"{result.get('title', '')}\n{result.get('snippet', '')}\n{result.get('source', '')}\n{result.get('query', '')}"
    score = 0
    reasons: list[str] = []

    whitelist = set(include_domains or []) or DEFAULT_A_SHARE_DOMAINS
    blacklist = set(exclude_domains or []) or DEFAULT_EXCLUDED_DOMAINS

    if any(term in text for term in A_SHARE_TERMS):
        score += 30
        reasons.append("含 A股交易相关词")
    if _domain_matches(domain, whitelist):
        score += 30
        reasons.append("来源域名属于 A股财经白名单")
    if STOCK_CODE_RE.search(text):
        score += 20
        reasons.append("含 A股股票代码")
    if any(term in text for term in OFFICIAL_AND_MEDIA_TERMS):
        score += 20
        reasons.append("含主流财经媒体或官方源名称")
    if any(term in text for term in TOPIC_TERMS):
        score += 20
        reasons.append("含题材关键词")

    if _domain_matches(domain, blacklist) or _looks_like_overseas_company_domain(domain):
        score -= 50
        reasons.append("来源属于百科、海外企业官网或排除域名")
    if any(re.search(pattern, str(result.get("title", ""))) for pattern in DEFINITION_PATTERNS):
        score -= 30
        reasons.append("标题疑似定义解释或科普文章")
    if any(term in text for term in PURE_SCIENCE_TERMS) and not any(term in text for term in TRADING_TERMS):
        score -= 30
        reasons.append("纯科普内容且缺少交易相关词")
    if not any(term in text for term in TRADING_TERMS):
        score -= 30
        reasons.append("缺少 A股/股票/上市公司/板块/公告/涨停等交易相关词")
    if any(term in text for term in TAIWAN_TERMS) and "A股" not in text:
        score -= 20
        reasons.append("疑似台湾或繁体财经内容且不涉及 A股")
    if ENGLISH_RE.match(str(result.get("title", "")) + str(result.get("snippet", ""))) and "A股" not in text:
        score -= 20
        reasons.append("纯英文且不涉及 A股产业链")

    score = max(0, min(100, score))
    if score >= 50:
        keep = "是"
        filter_reason = "保留：" + "；".join(reasons or ["达到 A股相关性阈值"])
        quality_bucket = "保留"
    elif score >= 30:
        keep = "否"
        filter_reason = "低相关候选，不进入观察池：" + "；".join(reasons or ["A股相关性不足"])
        quality_bucket = "低相关候选"
    else:
        keep = "否"
        filter_reason = "剔除：" + "；".join(reasons or ["A股相关性不足"])
        quality_bucket = "剔除"

    return {
        "a_share_score": score,
        "keep": keep,
        "filter_reason": filter_reason,
        "domain": domain,
        "quality_bucket": quality_bucket,
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


def _looks_like_overseas_company_domain(domain: str) -> bool:
    if not domain:
        return False
    if _domain_matches(domain, DEFAULT_A_SHARE_DOMAINS):
        return False
    overseas_suffixes = (".com", ".org", ".net", ".io", ".ai")
    china_hints = (".cn", "eastmoney", "cls.cn", "stcn", "cnstock", "cs.com.cn", "sse", "szse", "cninfo")
    return domain.endswith(overseas_suffixes) and not any(hint in domain for hint in china_hints)

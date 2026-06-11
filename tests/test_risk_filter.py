from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from main import load_config  # noqa: E402
from risk_filter import announcement_risk_flags, event_risk_flags  # noqa: E402


def _sources_config() -> dict:
    return load_config(PROJECT_ROOT / "config" / "sources.yaml")


def test_clarification_announcement_overrides_conflicting_news() -> None:
    event = {
        "event_id": "T101",
        "title": "网传云航科技参与低空管制系统订单",
        "content": "社媒消息称公司可能参与相关订单。",
        "topic_hint": "低空经济",
        "tickers": ["000001.SZ"],
        "source": "社媒模拟账号",
        "source_type": "social_media",
        "publish_time": "2026-06-08T11:05:00+08:00",
        "url": "https://example.com/social",
    }
    announcement = {
        "announcement_id": "A101",
        "company": "云航科技",
        "ticker": "000001.SZ",
        "title": "云航科技澄清不涉及低空管制系统业务",
        "content": "公司目前主营业务不涉及低空管制系统。",
        "announcement_type": "clarification_no_business",
        "source": "上市公司模拟公告",
        "source_type": "official_announcement",
        "publish_time": "2026-06-08T20:10:00+08:00",
        "url": "https://example.com/announcement",
    }

    flags = event_risk_flags(event, [announcement], _sources_config(), as_of="2026-06-08T23:59:59+08:00")
    risk_types = {risk["risk_type"] for risk in flags}

    assert "announcement_conflict" in risk_types
    assert "clarification_no_business" in risk_types


def test_announcement_risk_types_are_detected() -> None:
    reduction = {
        "announcement_type": "shareholder_reduction",
        "title": "股东减持计划公告",
        "content": "股东计划减持。",
    }
    regulatory = {
        "announcement_type": "regulatory_letter",
        "title": "收到监管函",
        "content": "交易所要求说明事项。",
    }

    assert announcement_risk_flags(reduction)[0]["risk_type"] == "shareholder_reduction"
    assert announcement_risk_flags(regulatory)[0]["risk_type"] == "regulatory_letter"


def test_positive_business_announcement_is_not_marked_as_reduction_or_regulatory() -> None:
    positive = {
        "announcement_type": "positive_business",
        "title": "签署项目合同",
        "content": "合同履行预计对未来经营业绩产生积极影响。",
    }

    risk_types = {risk["risk_type"] for risk in announcement_risk_flags(positive)}

    assert "shareholder_reduction" not in risk_types
    assert "regulatory_letter" not in risk_types

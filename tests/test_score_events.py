from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from main import load_config  # noqa: E402
from score_events import score_events  # noqa: E402


def _sources_config() -> dict:
    return load_config(PROJECT_ROOT / "config" / "sources.yaml")


def _rules() -> dict:
    return load_config(PROJECT_ROOT / "config" / "scoring_rules.yaml")


def _event(**overrides: object) -> dict:
    event = {
        "event_id": "T001",
        "title": "Test event",
        "content": "Test content",
        "topic_hint": "测试题材",
        "tickers": [],
        "source": "Test source",
        "source_type": "financial_news",
        "publish_time": "2026-06-08T10:00:00+08:00",
        "url": "https://example.com/test",
        "duplicate_count": 1,
    }
    event.update(overrides)
    return event


def _market() -> dict:
    return {"trade_date": "2026-06-08", "sectors": []}


def _score_one(event: dict, announcements: list[dict] | None = None, market: dict | None = None) -> dict:
    return score_events([event], announcements or [], market or _market(), _sources_config(), _rules())[0]


def test_official_announcement_gets_high_score() -> None:
    scored = _score_one(_event(source_type="official_announcement"))

    assert scored["confidence_score"] == 60
    assert scored["verification_status"] == "confirmed"
    assert "official_confirmation" in scored["reason"]


def test_social_rumor_gets_low_score() -> None:
    scored = _score_one(_event(source_type="social_sentiment"))
    risk_types = {risk["risk_type"] for risk in scored["risk_flags"]}

    assert scored["confidence_score"] == 0
    assert scored["verification_status"] == "rumor"
    assert "social_only" in risk_types


def test_two_or_more_independent_sources_add_bonus() -> None:
    single_source = _score_one(_event(event_id="T003A", duplicate_count=1))
    multi_source = _score_one(
        _event(
            event_id="T003B",
            duplicate_count=2,
            related_sources=["Source A", "Source B"],
        )
    )

    assert multi_source["confidence_score"] == single_source["confidence_score"] + 15
    assert "independent_sources_2_or_more" in multi_source["reason"]


def test_company_denial_subtracts_penalty_and_marks_contradicted() -> None:
    event = _event(event_id="T004", tickers=["000001.SZ"])
    no_denial = _score_one(event)
    with_denial = _score_one(
        event,
        [
            {
                "announcement_id": "A004",
                "company": "Test Co",
                "ticker": "000001.SZ",
                "title": "Clarification",
                "content": "Company denies related business involvement.",
                "announcement_type": "clarification_no_business",
                "source": "Official announcement",
                "source_type": "official_announcement",
                "publish_time": "2026-06-08T20:00:00+08:00",
                "url": "https://example.com/denial",
            }
        ],
    )
    risk_types = {risk["risk_type"] for risk in with_denial["risk_flags"]}

    assert with_denial["confidence_score"] < no_denial["confidence_score"]
    assert with_denial["verification_status"] == "contradicted"
    assert "company_denial" in risk_types


def test_old_news_subtracts_penalty_and_marks_stale() -> None:
    current = _score_one(_event(event_id="T005A", publish_time="2026-06-08T10:00:00+08:00"))
    stale = _score_one(_event(event_id="T005B", publish_time="2026-05-20T10:00:00+08:00"))
    risk_types = {risk["risk_type"] for risk in stale["risk_flags"]}

    assert stale["confidence_score"] < current["confidence_score"]
    assert stale["verification_status"] == "stale"
    assert "old_news" in risk_types


def test_reduction_announcement_subtracts_penalty() -> None:
    event = _event(event_id="T006", tickers=["300100.SZ"])
    no_reduction = _score_one(event)
    with_reduction = _score_one(
        event,
        [
            {
                "announcement_id": "A006",
                "company": "Test Co",
                "ticker": "300100.SZ",
                "title": "Reduction plan",
                "content": "Shareholder plans to reduce holdings.",
                "announcement_type": "shareholder_reduction",
                "source": "Official announcement",
                "source_type": "official_announcement",
                "publish_time": "2026-06-08T19:00:00+08:00",
                "url": "https://example.com/reduction",
            }
        ],
    )
    risk_types = {risk["risk_type"] for risk in with_reduction["risk_flags"]}

    assert with_reduction["confidence_score"] < no_reduction["confidence_score"]
    assert "reduction_announcement" in risk_types
    assert "reduction_announcement" in with_reduction["reason"]

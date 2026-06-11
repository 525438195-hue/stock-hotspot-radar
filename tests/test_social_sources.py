from score_events import score_events


def test_social_sentiment_event_stays_rumor_even_with_market_reaction():
    events = [
        {
            "event_id": "SOCIAL_001",
            "title": "某题材出现社媒传闻",
            "content": "社媒讨论某题材可能发酵，尚无官方公告。",
            "topic_hint": "机器人",
            "tickers": [],
            "source": "手动社媒情绪",
            "source_type": "social_sentiment",
            "publish_time": "2026-06-09T09:30:00+08:00",
            "url": "",
            "duplicate_count": 1,
        }
    ]
    market_data = {
        "trade_date": "2026-06-09",
        "sectors": [
            {
                "sector": "机器人",
                "change_pct": 4.2,
                "limit_up_count": 6,
                "turnover_change_pct": 25.0,
            }
        ],
    }
    rules = {
        "score_bounds": {"min": 0, "max": 100},
        "base_scores": {
            "social_sentiment": 5,
            "unknown": 0,
        },
        "source_type_aliases": {"social_sentiment": "social_sentiment"},
        "bonuses": {
            "market_volume_confirmed": 15,
            "sector_strength_confirmed": 15,
        },
        "penalties": {
            "social_only": 30,
            "high_position_chasing_risk": 20,
        },
        "market_thresholds": {
            "market_volume_confirmed_turnover_change_pct": 20,
            "sector_strength_change_pct": 3,
            "sector_strength_limit_up_count": 5,
            "high_position_change_pct": 5,
            "high_position_limit_up_count": 10,
        },
        "old_news_days": 7,
    }

    scored = score_events(events, [], market_data, {}, rules)

    assert scored[0]["verification_status"] == "rumor"
    assert "social_only" in {risk["risk_type"] for risk in scored[0]["risk_flags"]}
    assert scored[0]["confidence_score"] < 60

from __future__ import annotations

import pandas as pd

from watchlist_store import (
    WATCHLIST_FIELDS,
    add_or_update_stock,
    get_storage_mode,
    load_watchlist,
    save_watchlist,
)


def test_add_or_update_stock_uses_stock_code_as_key(tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHLIST_STORAGE", raising=False)

    added = add_or_update_stock(
        {
            "股票名称": "测试股份",
            "股票代码": "600000",
            "所属题材": "测试题材",
            "关注级别": "高",
            "持仓状态": "观察",
            "成本价": "",
            "备注": "第一版",
        },
        tmp_path,
    )
    updated = add_or_update_stock(
        {
            "股票名称": "测试股份",
            "股票代码": "600000",
            "所属题材": "更新题材",
            "关注级别": "中",
            "持仓状态": "持有",
            "成本价": "12.3",
            "备注": "第二版",
        },
        tmp_path,
    )

    df = load_watchlist(tmp_path)
    matched = df[df["股票代码"] == "600000"]
    assert added.action == "added"
    assert updated.action == "updated"
    assert len(matched) == 1
    assert matched.iloc[0]["所属题材"] == "更新题材"
    assert matched.iloc[0]["成本价"] == "12.3"


def test_save_watchlist_rejects_invalid_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHLIST_STORAGE", raising=False)
    before = load_watchlist(tmp_path)
    invalid = pd.DataFrame(
        [
            {
                "股票名称": "无效股票",
                "股票代码": "123",
                "所属题材": "测试",
                "关注级别": "高",
                "持仓状态": "观察",
                "成本价": "abc",
                "备注": "",
            }
        ],
        columns=WATCHLIST_FIELDS,
    )

    result = save_watchlist(invalid, tmp_path)
    after = load_watchlist(tmp_path)

    assert not result.success
    assert any("股票代码必须是 6 位数字" in error for error in result.errors)
    assert any("成本价必须是数字" in error for error in result.errors)
    assert after.to_dict("records") == before.to_dict("records")


def test_google_sheets_mode_returns_clear_message(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHLIST_STORAGE", "google_sheets")

    result = save_watchlist(pd.DataFrame(columns=WATCHLIST_FIELDS), tmp_path)

    assert get_storage_mode(tmp_path) == "google_sheets"
    assert not result.success
    assert "Google Sheets 存储尚未配置" in result.message

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from manual_input import load_manual_data  # noqa: E402


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_sample_mode_can_run() -> None:
    result = subprocess.run(
        [sys.executable, "src/main.py", "--mode", "sample"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert (PROJECT_ROOT / "outputs" / "daily_report.md").exists()
    assert (PROJECT_ROOT / "outputs" / "watchlist.csv").exists()
    assert (PROJECT_ROOT / "outputs" / "risk_flags.csv").exists()


def test_manual_mode_can_run() -> None:
    result = subprocess.run(
        [sys.executable, "src/main.py", "--mode", "manual"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "manual" in result.stdout
    assert (PROJECT_ROOT / "outputs" / "daily_report.md").exists()


def test_auto_mode_can_run_with_fallback() -> None:
    result = subprocess.run(
        [sys.executable, "src/main.py", "--mode", "auto"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "auto" in result.stdout
    assert (PROJECT_ROOT / "outputs" / "report_state.json").exists()


def test_empty_manual_csv_does_not_fail(tmp_path: Path) -> None:
    _write_csv(tmp_path / "manual_news.csv", ["标题", "正文", "来源", "来源类型", "发布时间", "原始链接", "相关关键词"], [])
    _write_csv(
        tmp_path / "manual_announcements.csv",
        ["公司名称", "股票代码", "公告标题", "公告正文", "公告类型", "发布时间", "原始链接"],
        [],
    )
    _write_csv(
        tmp_path / "manual_market.csv",
        ["板块名称", "板块涨幅", "涨停数量", "成交额", "放量幅度", "领涨股票", "领涨股票涨幅"],
        [],
    )

    events, announcements, market_data = load_manual_data(tmp_path)

    assert events == []
    assert announcements == []
    assert market_data["sectors"] == []
    assert market_data["empty_message"] == "暂无手动导入数据"


def test_chinese_manual_fields_are_loaded(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "manual_news.csv",
        ["标题", "正文", "来源", "来源类型", "发布时间", "原始链接", "相关关键词"],
        [
            {
                "标题": "低空经济政策继续推进",
                "正文": "政策文件提到低空经济基础设施。",
                "来源": "某财经媒体",
                "来源类型": "财经媒体",
                "发布时间": "2026-06-08 09:30",
                "原始链接": "https://example.com/news",
                "相关关键词": "低空经济,000001.SZ",
            }
        ],
    )
    _write_csv(
        tmp_path / "manual_announcements.csv",
        ["公司名称", "股票代码", "公告标题", "公告正文", "公告类型", "发布时间", "原始链接"],
        [
            {
                "公司名称": "云航科技",
                "股票代码": "000001.SZ",
                "公告标题": "澄清公告",
                "公告正文": "公司不涉及相关业务。",
                "公告类型": "澄清不涉及某业务",
                "发布时间": "2026-06-08 20:10",
                "原始链接": "https://example.com/ann",
            }
        ],
    )
    _write_csv(
        tmp_path / "manual_market.csv",
        ["板块名称", "板块涨幅", "涨停数量", "成交额", "放量幅度", "领涨股票", "领涨股票涨幅"],
        [
            {
                "板块名称": "低空经济",
                "板块涨幅": "3.5%",
                "涨停数量": "6",
                "成交额": "321.5亿元",
                "放量幅度": "24.8%",
                "领涨股票": "测试股份",
                "领涨股票涨幅": "10.0%",
            }
        ],
    )

    events, announcements, market_data = load_manual_data(tmp_path)

    assert events[0]["source_type"] == "financial_news"
    assert events[0]["publish_time"] == "2026-06-08T09:30:00+08:00"
    assert events[0]["topic_hint"] == "低空经济"
    assert events[0]["tickers"] == ["000001.SZ"]
    assert announcements[0]["announcement_type"] == "clarification_no_business"
    assert market_data["sectors"][0]["sector"] == "低空经济"
    assert market_data["sectors"][0]["turnover_amount_billion"] == 321.5

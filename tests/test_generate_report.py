from __future__ import annotations

import csv
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from main import main  # noqa: E402


def test_output_files_use_chinese_headers_and_values() -> None:
    main()

    watchlist = PROJECT_ROOT / "outputs" / "watchlist.csv"
    risk_flags = PROJECT_ROOT / "outputs" / "risk_flags.csv"
    report = PROJECT_ROOT / "outputs" / "daily_report.md"

    assert watchlist.read_bytes().startswith(b"\xef\xbb\xbf")
    assert risk_flags.read_bytes().startswith(b"\xef\xbb\xbf")

    with watchlist.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    assert reader.fieldnames == [
        "事件编号",
        "题材",
        "热点标题",
        "验证状态",
        "来源",
        "来源类型",
        "发布时间",
        "原始链接",
        "可信度分数",
        "风险标签",
        "评分原因",
        "重复次数",
        "对应板块",
        "板块涨幅",
        "涨停数量",
        "成交额",
        "放量幅度",
    ]
    assert rows
    assert {row["验证状态"] for row in rows}.issubset({"已确认", "部分确认", "未证实传闻", "被否定", "旧闻", "仅有市场反应"})
    assert any(row["来源类型"] == "政策文件" for row in rows)
    assert all("T" not in row["发布时间"] for row in rows)

    with risk_flags.open(encoding="utf-8-sig", newline="") as file:
        risk_reader = csv.DictReader(file)
        risk_rows = list(risk_reader)

    assert risk_reader.fieldnames == ["编号", "类型", "风险类型", "严重程度", "原因", "来源", "来源类型", "发布时间", "原始链接", "可信度分数"]
    assert risk_rows
    assert "监管风险" in {row["风险类型"] for row in risk_rows}

    report_text = report.read_text(encoding="utf-8")
    assert "confirmed" not in report_text
    assert "official_announcement" not in report_text
    assert "regulatory_warning" not in report_text
    assert "已确认" in report_text
    assert "公司公告" in report_text

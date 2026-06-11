import csv
import json
from pathlib import Path

from stock_candidate_builder import CANDIDATE_FIELDS, build_stock_candidates


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_stock_candidates_are_generated_with_safe_suggestions(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    data_dir = tmp_path / "data"
    output_dir.mkdir()
    data_dir.mkdir()
    _write_csv(
        output_dir / "watchlist.csv",
        [
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
        ],
        [
            {
                "事件编号": "E1",
                "题材": "机器人",
                "热点标题": "机器人板块走强",
                "验证状态": "部分确认",
                "来源": "财经媒体",
                "来源类型": "财经媒体",
                "发布时间": "2026-06-09 09:30",
                "原始链接": "",
                "可信度分数": "76",
                "风险标签": "无",
                "评分原因": "",
                "重复次数": "1",
                "对应板块": "机器人",
                "板块涨幅": "4.5",
                "涨停数量": "8",
                "成交额": "500亿元",
                "放量幅度": "30",
            }
        ],
    )
    _write_csv(output_dir / "risk_flags.csv", ["编号", "类型", "风险类型"], [])
    _write_csv(
        data_dir / "manual_market.csv",
        ["板块名称", "板块涨幅", "涨停数量", "成交额", "放量幅度", "领涨股票", "领涨股票涨幅"],
        [{"板块名称": "机器人", "板块涨幅": "4.5", "涨停数量": "8", "成交额": "500亿", "放量幅度": "30", "领涨股票": "测试机器人", "领涨股票涨幅": "10"}],
    )
    (output_dir / "report_state.json").write_text(json.dumps({"last_update_time": "2026-06-09 15:00"}), encoding="utf-8")

    path = build_stock_candidates(output_dir, data_dir)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))

    assert rows
    assert list(rows[0].keys()) == CANDIDATE_FIELDS
    assert rows[0]["观察建议"] in {"优先跟踪", "只看核心", "等待回踩", "暂不参与", "直接排除"}
    assert rows[0]["股票名称"] == "测试机器人"
    assert "观察建议" in rows[0]
    content = path.read_text(encoding="utf-8-sig")
    for forbidden in ["买入", "卖出", "推荐买", "满仓", "梭哈", "必涨", "明天涨停", "稳赚"]:
        assert forbidden not in content


def test_app_does_not_write_streamlit_objects() -> None:
    app_text = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")

    forbidden_snippets = [
        "st.write(main())",
        "st.write(st.container())",
        "st.write(st.columns(",
        "st.markdown(st.container())",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in app_text

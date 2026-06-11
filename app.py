"""Streamlit dashboard for A股热点个股雷达."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from main import run_pipeline as run_data_pipeline  # noqa: E402
from secrets_manager import export_missing_env_from_runtime, get_secret, runtime_secrets  # noqa: E402
from stock_candidate_builder import POSITIVE_SUGGESTIONS, build_stock_candidates  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORT_PATH = OUTPUT_DIR / "daily_report.md"
WATCHLIST_PATH = OUTPUT_DIR / "watchlist.csv"
RISK_FLAGS_PATH = OUTPUT_DIR / "risk_flags.csv"
STATE_PATH = OUTPUT_DIR / "report_state.json"
CANDIDATES_PATH = OUTPUT_DIR / "stock_candidates.csv"
SEARCH_DEDUPED_PATH = OUTPUT_DIR / "search_results_deduped.csv"

MAIN_COLUMNS = [
    "候选类型",
    "股票名称",
    "股票代码",
    "所属题材",
    "题材强度分",
    "可信度分数",
    "市场信号",
    "验证状态",
    "风险标签",
    "观察建议",
    "观察条件",
    "放弃条件",
]
FORBIDDEN_TERMS = ["买入", "卖出", "推荐买", "满仓", "梭哈", "必涨", "明天涨停", "稳赚"]
SUGGESTION_ORDER = {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "last_update_time": "暂无",
            "high_confidence_count": 0,
            "risk_announcement_count": 0,
            "source_success_rate": 0,
            "source_status": [],
            "warnings": ["尚未生成 report_state.json，请先运行 auto 模式"],
        }
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"last_update_time": "状态文件读取失败", "source_success_rate": 0, "source_status": []}


def ensure_runtime_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def has_tavily_key() -> bool:
    return bool(get_secret("TAVILY_API_KEY", PROJECT_ROOT).strip())


def state_is_today(state: dict[str, Any]) -> bool:
    value = str(state.get("last_update_time", "") or "")
    today = datetime.now().strftime("%Y-%m-%d")
    return value.startswith(today)


def csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            return sum(1 for _ in csv.DictReader(file))
    except Exception:
        return 0


def today_data_generated(state: dict[str, Any]) -> bool:
    return (
        state_is_today(state)
        and STATE_PATH.exists()
        and CANDIDATES_PATH.exists()
        and SEARCH_DEDUPED_PATH.exists()
        and csv_row_count(CANDIDATES_PATH) > 0
    )


def should_auto_refresh(state: dict[str, Any]) -> bool:
    if not STATE_PATH.exists() or not CANDIDATES_PATH.exists() or not SEARCH_DEDUPED_PATH.exists():
        return True
    if not state_is_today(state):
        return True
    if has_tavily_key() and (csv_row_count(CANDIDATES_PATH) == 0 or int(state.get("raw_count", 0) or 0) == 0):
        return True
    return False


def run_auto_mode() -> tuple[bool, str]:
    export_missing_env_from_runtime(PROJECT_ROOT)
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            run_data_pipeline("auto")
        st.session_state["last_auto_error"] = ""
        st.session_state["last_auto_log"] = buffer.getvalue()[-6000:]
        return True, ""
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        log = buffer.getvalue()
        trace = traceback.format_exc(limit=8)
        st.session_state["last_auto_error"] = detail
        st.session_state["last_auto_log"] = (log + "\n" + trace)[-6000:]
        return False, detail


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return [_sanitize_row(row) for row in csv.DictReader(file)]


def ensure_candidates() -> None:
    if not CANDIDATES_PATH.exists() and WATCHLIST_PATH.exists():
        build_stock_candidates(OUTPUT_DIR, DATA_DIR)


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    secrets = export_missing_env_from_runtime(PROJECT_ROOT)
    env = dict(os.environ)
    for key, value in secrets.items():
        if value:
            env.setdefault(key, value)
    return subprocess.run(
        [sys.executable, *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def regenerate_candidates() -> Path:
    return build_stock_candidates(OUTPUT_DIR, DATA_DIR)


def _score(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("可信度分数", "0") or 0))
    except ValueError:
        return 0


def _strength(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("题材强度分", "0") or 0))
    except ValueError:
        return 0


def apply_sidebar_filters(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    filtered = list(rows)
    with st.sidebar:
        st.header("筛选")
        topics = sorted({row.get("所属题材", "") for row in rows if row.get("所属题材")})
        selected_topics = st.multiselect("按题材筛选", topics)
        if selected_topics:
            filtered = [row for row in filtered if row.get("所属题材") in selected_topics]

        min_score = st.slider("按可信度分数筛选", 0, 100, 0)
        filtered = [row for row in filtered if _score(row) >= min_score]

        statuses = sorted({row.get("验证状态", "") for row in rows if row.get("验证状态")})
        selected_statuses = st.multiselect("按验证状态筛选", statuses)
        if selected_statuses:
            filtered = [row for row in filtered if row.get("验证状态") in selected_statuses]

        risks = sorted(
            {
                part.strip()
                for row in rows
                for part in row.get("风险标签", "").split("；")
                if part.strip() and part.strip() != "无"
            }
        )
        selected_risks = st.multiselect("按风险标签筛选", risks)
        if selected_risks:
            filtered = [row for row in filtered if any(risk in row.get("风险标签", "") for risk in selected_risks)]

        only_priority = st.checkbox("是否只看优先跟踪")
        if only_priority:
            filtered = [row for row in filtered if row.get("观察建议") == "优先跟踪"]

        show_excluded = st.checkbox("是否显示暂不参与和直接排除", value=True)
        if not show_excluded:
            filtered = [row for row in filtered if row.get("观察建议") in POSITIVE_SUGGESTIONS]

    return sort_candidates(filtered)


def render_header(state: dict[str, Any], candidates: list[dict[str, str]], risk_rows: list[dict[str, str]]) -> None:
    st.title("A股热点个股雷达")
    st.caption("仅生成热点观察池，不构成买卖建议，不自动下单。")
    cols = st.columns(5)
    cols[0].metric("更新时间", str(state.get("last_update_time", "暂无")))
    cols[1].metric(
        "今日观察股数量",
        len([row for row in candidates if row.get("候选类型") == "个股" and row.get("观察建议") in POSITIVE_SUGGESTIONS]),
    )
    cols[2].metric("高可信热点数量", int(state.get("high_confidence_count", 0) or 0))
    cols[3].metric("风险股票数量", len(risk_rows))
    cols[4].metric("数据源成功率", f"{float(state.get('source_success_rate', 0) or 0) * 100:.0f}%")


def render_runtime_status(tavily_ready: bool, data_ready: bool) -> None:
    last_error = str(st.session_state.get("last_auto_error", "") or "无")
    cols = st.columns(3)
    cols[0].metric("Tavily Key 是否已读取", "是" if tavily_ready else "否")
    cols[1].metric("今日数据是否已生成", "是" if data_ready else "否")
    cols[2].metric("最近一次错误原因", last_error[:80])


def render_actions() -> None:
    cols = st.columns(4)
    with cols[0]:
        if st.button("立即刷新今日数据", use_container_width=True):
            ok, error = run_auto_mode()
            if ok:
                regenerate_candidates()
                st.success("联网刷新完成，观察池已更新")
                st.rerun()
            else:
                st.error(f"联网刷新失败：{error}")

    with cols[1]:
        if st.button("重新生成观察池", use_container_width=True):
            regenerate_candidates()
            st.success("观察池已重新生成")
            st.rerun()

    with cols[2]:
        if CANDIDATES_PATH.exists():
            st.download_button(
                "导出观察池",
                data=CANDIDATES_PATH.read_bytes(),
                file_name="stock_candidates.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.button("导出观察池", disabled=True, use_container_width=True)

    with cols[3]:
        if st.button("查看原始日报", use_container_width=True):
            st.session_state["show_report"] = True


def render_focus_table(rows: list[dict[str, str]]) -> None:
    st.subheader("今日重点观察股")
    focus = [row for row in rows if row.get("候选类型") == "个股" and row.get("观察建议") in POSITIVE_SUGGESTIONS]
    if not focus:
        st.info("暂无观察股，请先运行 auto 模式或填写 manual 数据。")
        return
    risky_count = sum(1 for row in focus if row.get("风险标签", "无") not in {"", "无"})
    if risky_count:
        st.warning(f"{risky_count} 条观察股带有风险标签，请先人工复核。")
    st.dataframe(_select_columns(_risk_display(focus), MAIN_COLUMNS), use_container_width=True, hide_index=True)


def render_theme_observation(rows: list[dict[str, str]]) -> None:
    st.subheader("题材观察")
    themes = [row for row in rows if row.get("候选类型") == "题材"]
    if not themes:
        st.info("暂无题材级观察线索。")
        return
    columns = ["所属题材", "题材强度分", "可信度分数", "市场信号", "信息来源", "验证状态", "观察建议", "观察条件", "放弃条件"]
    st.dataframe(_select_columns(themes, columns), use_container_width=True, hide_index=True)


def render_theme_rank(rows: list[dict[str, str]]) -> None:
    st.subheader("题材热度排行")
    best_by_topic: dict[str, dict[str, str]] = {}
    for row in [item for item in rows if item.get("候选类型") != "占位"]:
        topic = row.get("所属题材", "未识别题材")
        current = best_by_topic.get(topic)
        if current is None or _strength(row) > _strength(current):
            best_by_topic[topic] = row
    ranking = []
    for row in best_by_topic.values():
        ranking.append(
            {
                "题材": row.get("所属题材", ""),
                "涨幅": _extract_signal(row.get("市场信号", ""), "板块涨幅"),
                "涨停数量": _extract_signal(row.get("市场信号", ""), "涨停数量"),
                "成交额": _extract_signal(row.get("市场信号", ""), "成交额"),
                "领涨股票": row.get("股票名称", ""),
                "题材阶段": row.get("题材阶段", ""),
            }
        )
    ranking.sort(key=lambda item: _score({"可信度分数": str(_strength(best_by_topic.get(item["题材"], {}))) }), reverse=True)
    if ranking:
        st.dataframe(ranking, use_container_width=True, hide_index=True)
    else:
        st.info("暂无题材热度数据。")


def render_risk_section(risk_rows: list[dict[str, str]]) -> None:
    st.subheader("风险股票 / 风险公告")
    if risk_rows:
        st.dataframe(risk_rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无风险公告。")


def render_excluded(rows: list[dict[str, str]]) -> None:
    st.subheader("暂不参与 / 直接排除")
    excluded = [row for row in rows if row.get("候选类型") != "占位" and row.get("观察建议") in {"暂不参与", "直接排除"}]
    if excluded:
        columns = ["股票名称", "股票代码", "所属题材", "可信度分数", "验证状态", "风险标签", "观察建议", "放弃条件"]
        st.dataframe(_select_columns(_risk_display(excluded), columns), use_container_width=True, hide_index=True)
    else:
        st.info("暂无暂不参与或直接排除项。")


def render_rumors(rows: list[dict[str, str]]) -> None:
    st.subheader("未证实传闻与社媒小道消息")
    rumors = [
        row
        for row in rows
        if row.get("验证状态") == "未证实传闻" or "社媒" in row.get("信息来源", "") or "仅社媒" in row.get("风险标签", "")
    ]
    if not rumors:
        st.info("暂无未证实传闻或社媒小道消息。")
        return
    st.warning("以下内容未证实，不进入高可信观察池。")
    columns = ["所属题材", "股票名称", "信息来源", "验证状态", "风险标签", "观察建议", "备注"]
    st.dataframe(_select_columns(rumors, columns), use_container_width=True, hide_index=True)


def render_filtered_search_results() -> None:
    with st.expander("低质量/被过滤搜索结果", expanded=False):
        rows = load_csv_rows(SEARCH_DEDUPED_PATH)
        filtered = [row for row in rows if row.get("是否保留") != "是"]
        if not filtered:
            st.info("暂无被过滤搜索结果。")
            return
        columns = ["标题", "来源", "域名", "查询词", "A股相关性分数", "是否保留", "过滤原因"]
        st.dataframe(_select_columns(filtered, columns), use_container_width=True, hide_index=True)


def render_placeholder_debug(rows: list[dict[str, str]]) -> None:
    placeholders = [row for row in rows if row.get("候选类型") == "占位"]
    if not placeholders:
        return
    with st.expander("占位候选调试", expanded=False):
        columns = ["候选类型", "股票名称", "所属题材", "可信度分数", "观察建议", "备注"]
        st.dataframe(_select_columns(placeholders, columns), use_container_width=True, hide_index=True)


def render_report() -> None:
    expanded = bool(st.session_state.get("show_report", False))
    with st.expander("原始日报", expanded=expanded):
        if REPORT_PATH.exists():
            st.markdown(_sanitize_text(REPORT_PATH.read_text(encoding="utf-8")))
        else:
            st.info("暂无原始日报。")


def render_cloud_debug(tavily_ready: bool) -> None:
    with st.expander("云端运行状态", expanded=False):
        rows = [
            {"检查项": "是否读取到 TAVILY_API_KEY", "状态": "是" if tavily_ready else "否"},
            {"检查项": "outputs 是否存在", "状态": "是" if OUTPUT_DIR.exists() else "否"},
            {"检查项": "report_state.json 是否存在", "状态": "是" if STATE_PATH.exists() else "否"},
            {"检查项": "stock_candidates.csv 行数", "状态": str(csv_row_count(CANDIDATES_PATH))},
            {"检查项": "search_results_deduped.csv 行数", "状态": str(csv_row_count(SEARCH_DEDUPED_PATH))},
            {"检查项": "最近一次 auto 模式错误", "状态": str(st.session_state.get("last_auto_error", "") or "无")},
        ]
        st.table(rows)
        log = str(st.session_state.get("last_auto_log", "") or "")
        if log:
            st.caption("最近一次 auto 模式日志")
            st.code(log[-4000:])


def sort_candidates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(rows, key=lambda row: (SUGGESTION_ORDER.get(row.get("观察建议", ""), 9), -_score(row), row.get("所属题材", "")))


def _select_columns(rows: list[dict[str, str]], columns: list[str]) -> list[dict[str, str]]:
    return [{column: row.get(column, "") for column in columns} for row in rows]


def _risk_display(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        item = dict(row)
        risk = item.get("风险标签", "")
        if risk and risk != "无" and not risk.startswith("风险："):
            item["风险标签"] = f"风险：{risk}"
        result.append(item)
    return result


def _extract_signal(signal: str, name: str) -> str:
    for part in signal.split("；"):
        if part.startswith(name):
            return part.replace(name, "", 1).strip()
    return ""


def _sanitize_row(row: dict[str, str]) -> dict[str, str]:
    return {key: _sanitize_text(value) for key, value in row.items()}


def _sanitize_text(text: object) -> str:
    value = str(text or "")
    replacements = {
        "买入": "观察",
        "卖出": "降低权重",
        "推荐买": "观察",
        "满仓": "风险较高",
        "梭哈": "风险较高",
        "必涨": "风险较高",
        "明天涨停": "风险较高",
        "稳赚": "风险较高",
    }
    for forbidden, replacement in replacements.items():
        value = value.replace(forbidden, replacement)
    return value


def main() -> None:
    st.set_page_config(page_title="A股热点个股雷达", layout="wide")
    st.markdown("<meta http-equiv='refresh' content='300'>", unsafe_allow_html=True)
    ensure_runtime_dirs()
    export_missing_env_from_runtime(PROJECT_ROOT)
    tavily_ready = has_tavily_key()
    if not tavily_ready:
        st.warning("当前未配置 Tavily API Key，无法联网搜索。")

    state = load_state()
    if should_auto_refresh(state) and not st.session_state.get("auto_refresh_attempted", False):
        st.session_state["auto_refresh_attempted"] = True
        with st.spinner("正在自动刷新今日数据..."):
            ok, error = run_auto_mode()
        if ok:
            st.success("今日数据已自动刷新。")
            state = load_state()
        else:
            st.error(f"自动刷新失败：{error}")

    ensure_candidates()

    candidates = apply_sidebar_filters(load_csv_rows(CANDIDATES_PATH))
    all_candidates = sort_candidates(load_csv_rows(CANDIDATES_PATH))
    risk_rows = load_csv_rows(RISK_FLAGS_PATH)
    data_ready = today_data_generated(load_state())

    render_header(state, all_candidates, risk_rows)
    render_runtime_status(tavily_ready, data_ready)
    render_actions()
    render_focus_table(candidates)
    render_theme_observation(candidates)
    render_theme_rank(candidates)
    render_risk_section(risk_rows)
    render_excluded(candidates)
    render_rumors(candidates)
    render_filtered_search_results()
    render_placeholder_debug(candidates)
    render_report()
    render_cloud_debug(tavily_ready)


if __name__ == "__main__":
    main()

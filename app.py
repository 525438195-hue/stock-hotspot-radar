"""Streamlit dashboard for A股热点个股雷达."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import subprocess
import sys
import textwrap
import traceback
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from main import run_pipeline as run_data_pipeline  # noqa: E402
from secrets_manager import export_missing_env_from_runtime, get_secret, runtime_secrets  # noqa: E402
from stock_candidate_builder import POSITIVE_SUGGESTIONS, build_stock_candidates  # noqa: E402
from time_utils import format_publish_time  # noqa: E402
from watchlist_monitor import run_watchlist_monitor  # noqa: E402
from watchlist_store import (  # noqa: E402
    LEVEL_OPTIONS,
    POSITION_OPTIONS,
    WATCHLIST_FIELDS,
    add_or_update_stock,
    csv_cloud_warning,
    delete_stock,
    get_storage_mode,
    google_sheets_message,
    load_watchlist as load_watchlist_store,
    save_watchlist,
)


OUTPUT_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORT_PATH = OUTPUT_DIR / "daily_report.md"
WATCHLIST_PATH = OUTPUT_DIR / "watchlist.csv"
RISK_FLAGS_PATH = OUTPUT_DIR / "risk_flags.csv"
STATE_PATH = OUTPUT_DIR / "report_state.json"
CANDIDATES_PATH = OUTPUT_DIR / "stock_candidates.csv"
SEARCH_DEDUPED_PATH = OUTPUT_DIR / "search_results_deduped.csv"
NEWS_SUMMARY_PATH = OUTPUT_DIR / "news_summary.csv"
WATCHLIST_DATA_PATH = DATA_DIR / "watchlist.csv"
WATCHLIST_NEWS_PATH = OUTPUT_DIR / "watchlist_news.csv"
WATCHLIST_REVIEW_PATH = OUTPUT_DIR / "watchlist_review.csv"

MAIN_COLUMNS = [
    "股票名称",
    "股票代码",
    "所属题材",
    "可信度分数",
    "观察建议",
    "风险标签",
    "相关新闻标题",
    "发布时间",
]
FORBIDDEN_TERMS = ["买入", "卖出", "推荐买", "满仓", "梭哈", "必涨", "明天涨停", "稳赚"]
SUGGESTION_ORDER = {"优先跟踪": 0, "只看核心": 1, "等待回踩": 2, "暂不参与": 3, "直接排除": 4}


def _render_html(html_string: str) -> None:
    html_text = textwrap.dedent(html_string).strip()
    st.markdown(html_text, unsafe_allow_html=True)


def inject_global_css() -> None:
    _render_html(
        """
        <style>
        :root {
            --radar-bg: #F6F8FB;
            --radar-card: #FFFFFF;
            --radar-text: #1F2937;
            --radar-muted: #6B7280;
            --radar-primary: #2563EB;
            --radar-risk: #EF4444;
            --radar-success: #10B981;
            --radar-warning: #F59E0B;
            --radar-border: #E5E7EB;
        }
        .stApp { background: var(--radar-bg); color: var(--radar-text); }
        .block-container {
            max-width: 1180px;
            padding: 1.25rem 1.25rem 3rem;
            margin: 0 auto;
        }
        section[data-testid="stSidebar"] {
            background: #FFFFFF;
            border-right: 1px solid var(--radar-border);
        }
        div[data-testid="stTabs"] button {
            border-radius: 999px;
            color: var(--radar-muted);
            font-weight: 650;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--radar-primary);
        }
        .stButton > button,
        .stDownloadButton > button,
        div[data-testid="stFormSubmitButton"] button {
            border-radius: 10px !important;
            font-weight: 650 !important;
            border: 1px solid #D1D5DB !important;
            box-shadow: none !important;
        }
        .stButton > button[kind="primary"],
        div[data-testid="stFormSubmitButton"] button[kind="primary"] {
            background: var(--radar-primary) !important;
            color: #FFFFFF !important;
            border-color: var(--radar-primary) !important;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            border-radius: 16px;
            overflow: hidden;
            border: 1px solid var(--radar-border);
            background: #FFFFFF;
        }
        .radar-hero {
            background: linear-gradient(135deg, #FFFFFF 0%, #EFF6FF 100%);
            border: 1px solid #DBEAFE;
            border-radius: 16px;
            box-shadow: 0 12px 30px rgba(37, 99, 235, 0.08);
            padding: 28px;
            margin-bottom: 18px;
        }
        .radar-hero h1 {
            color: var(--radar-text);
            font-size: 2rem;
            line-height: 1.15;
            margin: 0 0 8px;
            letter-spacing: 0;
        }
        .radar-hero p {
            color: var(--radar-muted);
            font-size: 1rem;
            margin: 0 0 16px;
        }
        .radar-pill-row,
        .radar-badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }
        .radar-badge,
        .radar-pill {
            display: inline-flex;
            align-items: center;
            width: fit-content;
            max-width: 100%;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 0.78rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .radar-pill { padding: 6px 12px; }
        .badge-blue, .pill-blue { background: #DBEAFE; color: #1D4ED8; }
        .badge-green, .pill-green { background: #D1FAE5; color: #047857; }
        .badge-red, .pill-red { background: #FEE2E2; color: #B91C1C; }
        .badge-orange, .pill-orange { background: #FEF3C7; color: #B45309; }
        .badge-gray, .pill-gray { background: #F3F4F6; color: #4B5563; }
        .radar-metric-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
            margin: 12px 0 18px;
        }
        .radar-metric-card,
        .radar-card,
        .radar-empty-card,
        .news-card,
        .watch-card {
            background: var(--radar-card);
            border: 1px solid var(--radar-border);
            border-radius: 16px;
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.05);
        }
        .radar-metric-card {
            padding: 16px;
            min-height: 118px;
        }
        .metric-top {
            display: flex;
            justify-content: space-between;
            color: var(--radar-muted);
            font-size: 0.82rem;
            gap: 8px;
        }
        .metric-value {
            color: var(--radar-text);
            font-size: 1.65rem;
            font-weight: 800;
            line-height: 1.15;
            margin: 10px 0 6px;
            word-break: break-word;
        }
        .metric-desc {
            color: var(--radar-muted);
            font-size: 0.82rem;
            line-height: 1.4;
        }
        .radar-empty-card {
            padding: 22px;
            color: var(--radar-muted);
            margin: 8px 0 14px;
        }
        .radar-empty-card strong {
            display: block;
            color: var(--radar-text);
            font-size: 1.05rem;
            margin-bottom: 6px;
        }
        .news-card,
        .watch-card {
            padding: 16px;
            margin-bottom: 12px;
        }
        .news-title,
        .watch-title {
            color: var(--radar-text);
            font-weight: 800;
            font-size: 1rem;
            line-height: 1.45;
            margin-bottom: 10px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .watch-title { font-size: 1.08rem; }
        .news-meta,
        .watch-meta {
            color: var(--radar-muted);
            font-size: 0.84rem;
            line-height: 1.55;
            margin: 8px 0;
        }
        .watch-counts {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
            margin: 12px 0;
        }
        .watch-count {
            background: #F9FAFB;
            border-radius: 12px;
            padding: 8px;
            text-align: center;
            color: var(--radar-muted);
            font-size: 0.76rem;
        }
        .watch-count strong {
            display: block;
            color: var(--radar-text);
            font-size: 1.05rem;
        }
        .card-link {
            color: var(--radar-primary);
            font-weight: 750;
            text-decoration: none;
        }
        .section-card {
            background: #FFFFFF;
            border: 1px solid var(--radar-border);
            border-radius: 16px;
            box-shadow: 0 8px 22px rgba(15, 23, 42, 0.04);
            padding: 16px;
            margin-bottom: 12px;
        }
        @media (max-width: 768px) {
            .block-container {
                padding: 0.85rem 0.7rem 2rem;
                max-width: 100%;
            }
            .radar-hero {
                padding: 20px 16px;
                border-radius: 14px;
            }
            .radar-hero h1 { font-size: 1.55rem; }
            .radar-hero p { font-size: 0.92rem; }
            .radar-metric-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
            }
            .radar-metric-card { min-height: 102px; padding: 13px; }
            .metric-value { font-size: 1.28rem; }
            .watch-counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            section[data-testid="stSidebar"] {
                width: min(88vw, 320px) !important;
            }
            div[data-testid="stDataFrame"] {
                overflow-x: auto;
            }
        }
        @media (max-width: 460px) {
            .radar-metric-grid { grid-template-columns: 1fr; }
            .radar-badge, .radar-pill { white-space: normal; }
        }
        </style>
        """
    )


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
    load_watchlist_store(PROJECT_ROOT)


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
            run_watchlist_monitor(PROJECT_ROOT)
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


@st.cache_data(ttl=300)
def load_cached_csv_rows(path_text: str, mtime: float) -> list[dict[str, str]]:
    del mtime
    path = Path(path_text)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return [_sanitize_row(row) for row in csv.DictReader(file)]


def cached_csv_rows(path: Path) -> list[dict[str, str]]:
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return load_cached_csv_rows(str(path), mtime)


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


def _source_status_table(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("source_status_table")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    legacy_rows = state.get("source_status")
    if not isinstance(legacy_rows, list):
        return []
    normalized_rows: list[dict[str, Any]] = []
    for item in legacy_rows:
        if not isinstance(item, dict):
            continue
        status = _source_status(item)
        normalized_rows.append(
            {
                "source_name": str(item.get("source_name") or item.get("source") or ""),
                "source_type": str(item.get("source_type") or "unknown"),
                "status": status,
                "count": int(item.get("item_count", 0) or item.get("count", 0) or 0),
                "counted_in_success_rate": status in {"success", "failed", "timeout"},
                "reason": str(item.get("reason") or item.get("warning") or "无"),
                "note": _source_status_note(item, status),
            }
        )
    return normalized_rows


def _source_status(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip()
    reason = str(item.get("reason") or item.get("warning") or "")
    if status in {"success", "failed", "timeout", "skipped", "placeholder", "fallback"}:
        return status
    if "占位源" in reason or "未接入真实接口" in reason:
        return "placeholder"
    if "未启用" in reason or "未配置" in reason or "URL 为空" in reason:
        return "skipped"
    if item.get("success"):
        return "success"
    return "failed"


def _source_status_note(item: dict[str, Any], status: str) -> str:
    source_name = str(item.get("source_name") or item.get("source") or "")
    if status == "placeholder":
        return "未接入真实接口"
    if status == "skipped":
        return "未启用或配置不完整，不计入成功率"
    if status == "fallback":
        return str(item.get("note") or "兜底数据")
    if "Tavily" in source_name:
        return "主搜索源"
    if status == "timeout":
        return "超时，若有 fallback 会单独显示"
    if status == "failed":
        return "启用源请求失败，计入成功率"
    return "启用源成功返回有效数据"


def _source_type_text(value: object) -> str:
    labels = {
        "government_policy": "政策文件",
        "official_announcement": "公司公告",
        "exchange_announcement": "交易所公告",
        "financial_news": "财经媒体",
        "industry_news": "行业媒体",
        "search_api": "搜索API",
        "overseas_news": "海外新闻",
        "social_sentiment": "社媒情绪",
        "market_data": "行情数据",
        "fallback": "fallback",
        "unknown": "未知来源",
    }
    return labels.get(str(value), str(value or "未知来源"))


def _enabled_source_rate_text(state: dict[str, Any]) -> str:
    value = state.get("enabled_source_success_rate")
    if value is None:
        rows = _source_status_table(state)
        active = [row for row in rows if row.get("counted_in_success_rate")]
        if not active:
            return "暂无启用数据源"
        success_count = sum(1 for row in active if row.get("status") == "success")
        value = success_count / len(active)
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "暂无启用数据源"


def _tavily_row(state: dict[str, Any]) -> dict[str, Any] | None:
    for row in _source_status_table(state):
        if "Tavily" in str(row.get("source_name", "")):
            return row
    return None


def _tavily_status_text(state: dict[str, Any]) -> str:
    row = _tavily_row(state)
    if row and row.get("status") == "success":
        return "成功"
    if row and row.get("status") == "failed":
        return "失败"
    if row and row.get("status") == "timeout":
        return "超时"
    if row and row.get("status") == "skipped":
        return "未配置"
    return "待刷新" if has_tavily_key() else "未配置"


def _tavily_result_count(state: dict[str, Any]) -> int:
    if state.get("tavily_result_count") is not None:
        try:
            return int(state.get("tavily_result_count") or 0)
        except (TypeError, ValueError):
            pass
    row = _tavily_row(state)
    if not row:
        return 0
    return int(row.get("count", 0) or 0)


def _fallback_text(state: dict[str, Any]) -> str:
    values = state.get("fallback_used") or state.get("fallback_usage") or []
    if not values:
        rows = _source_status_table(state)
        values = [row.get("source_name") for row in rows if row.get("status") == "fallback"]
    if not values:
        return "未使用"
    return "、".join(str(value) for value in values if value) or "未使用"


def _stock_candidate_count(candidates: list[dict[str, str]], state: dict[str, Any]) -> int:
    count = len([row for row in candidates if row.get("候选类型") in {"个股", "个股待补代码"}])
    if count:
        return count
    try:
        return int(state.get("stock_candidate_count") or 0)
    except (TypeError, ValueError):
        return 0


def _last_refresh_seconds(state: dict[str, Any]) -> str:
    timing = state.get("refresh_timing", {}) if isinstance(state.get("refresh_timing", {}), dict) else {}
    try:
        seconds = float(timing.get("total_seconds", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds:.1f} 秒" if seconds else "暂无"


def _hot_news_count() -> int:
    rows = load_csv_rows(SEARCH_DEDUPED_PATH)
    return len([row for row in rows if row.get("是否保留") == "是" or row.get("结果类型") == "高质量新闻"])


def _cache_status_text() -> str:
    value = st.session_state.get("used_cache")
    if value is None:
        return "是"
    return "是" if value else "否"


def render_header(
    state: dict[str, Any],
    candidates: list[dict[str, str]],
    risk_rows: list[dict[str, str]],
    *,
    tavily_ready: bool,
    data_ready: bool,
) -> None:
    status_html = "".join(
        [
            _pill("Tavily 已连接" if tavily_ready else "Tavily 未连接", "green" if tavily_ready else "orange"),
            _pill("今日已刷新" if data_ready else "今日未刷新", "green" if data_ready else "gray"),
            _pill("使用缓存" if _cache_status_text() == "是" else "实时刷新", "blue" if _cache_status_text() == "是" else "orange"),
        ]
    )
    _render_html(
        f"""
        <div class="radar-hero">
            <h1>A股热点个股雷达</h1>
            <p>热点观察 / 自选股情报 / 风险提示，仅供复盘和观察，不构成买卖建议。</p>
            <div class="radar-pill-row">{status_html}</div>
        </div>
        """
    )
    watchlist_count = len(load_watchlist_store(PROJECT_ROOT))
    risk_count = len(risk_rows) + int(state.get("watchlist_risk_count", 0) or 0)
    render_metric_cards(
        [
            ("今日热点新闻数量", str(_hot_news_count()), "保留新闻与高质量新闻", "N"),
            ("个股候选数量", str(_stock_candidate_count(candidates, state)), "来自观察池候选", "S"),
            ("自选股数量", str(watchlist_count), "我的自选股列表", "W"),
            ("风险提示数量", str(risk_count), "公告与自选股风险", "R"),
            ("上次刷新时间", str(state.get("last_update_time", "暂无")), "缓存状态用于页面展示", "T"),
            ("上次刷新耗时", _last_refresh_seconds(state), "最近一次刷新流程耗时", "Z"),
        ]
    )


def render_metric_cards(items: list[tuple[str, str, str, str]]) -> None:
    cards = []
    for title, value, desc, icon in items:
        cards.append(
            f"""
            <div class="radar-metric-card">
                <div class="metric-top"><span>{_escape(title)}</span><span>{_escape(icon)}</span></div>
                <div class="metric-value">{_escape(value)}</div>
                <div class="metric-desc">{_escape(desc)}</div>
            </div>
            """
        )
    _render_html(f"<div class=\"radar-metric-grid\">{''.join(cards)}</div>")


def render_runtime_status(tavily_ready: bool, data_ready: bool) -> None:
    last_error = str(st.session_state.get("last_auto_error", "") or "无")
    cols = st.columns(3)
    cols[0].metric("Tavily Key 是否已读取", "是" if tavily_ready else "否")
    cols[1].metric("今日数据是否已生成", "是" if data_ready else "否")
    cols[2].metric("最近一次错误原因", last_error[:80])


def render_source_status(state: dict[str, Any], candidates: list[dict[str, str]]) -> None:
    st.subheader("数据源状态")
    coverage = state.get("coverage_report", {}) if isinstance(state.get("coverage_report", {}), dict) else {}
    deduped_rows = load_csv_rows(SEARCH_DEDUPED_PATH)
    high_quality_count = len([row for row in deduped_rows if row.get("结果类型") == "高质量新闻"])
    stock_count = len([row for row in candidates if row.get("候选类型") in {"个股", "个股待补代码"}])
    status_cols = st.columns(5)
    status_cols[0].metric("Tavily 状态", _tavily_status_text(state))
    status_cols[1].metric("搜索 query 数量", int(coverage.get("searched_queries_count") or coverage.get("search_queries_count") or 0))
    status_cols[2].metric("原始结果数量", int(coverage.get("raw_results_count", 0) or 0))
    status_cols[3].metric("去重后结果数量", int(state.get("deduped_result_count", 0) or len(deduped_rows)))
    status_cols[4].metric("启用源成功率", _enabled_source_rate_text(state))
    status_cols2 = st.columns(4)
    status_cols2[0].metric("保留结果数量", len([row for row in deduped_rows if row.get("是否保留") == "是"]))
    status_cols2[1].metric("高质量新闻数量", high_quality_count)
    status_cols2[2].metric("个股候选数量", stock_count)
    status_cols2[3].metric("最近一次错误原因", str(st.session_state.get("last_auto_error", "") or "无")[:80])

    rows = []
    for row in _source_status_table(state):
        status = str(row.get("status", ""))
        rows.append(
            {
                "数据源名称": row.get("source_name", ""),
                "类型": _source_type_text(row.get("source_type")),
                "状态": status,
                "是否计入成功率": "是" if row.get("counted_in_success_rate") else "否",
                "返回数量": int(row.get("count", 0) or 0),
                "失败原因": row.get("reason", "无"),
                "备注": row.get("note", ""),
            }
        )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无数据源状态，请先刷新今日数据。")


def render_actions() -> None:
    cols = st.columns(4)
    with cols[0]:
        if st.button("立即刷新今日数据", type="primary", use_container_width=True):
            start = time.perf_counter()
            progress = st.progress(0)
            with st.status("正在刷新今日数据...", expanded=True) as status:
                st.write("正在生成搜索关键词")
                progress.progress(10)
                st.write("正在请求 Tavily")
                progress.progress(25)
                ok, error = run_auto_mode()
                elapsed = time.perf_counter() - start
                if ok:
                    st.write("正在清洗和过滤新闻")
                    progress.progress(70)
                    st.write("正在生成观察池")
                    progress.progress(82)
                    regenerate_candidates()
                    st.write("正在生成新闻总结")
                    progress.progress(90)
                    st.write("正在写入缓存文件")
                    progress.progress(98)
                    progress.progress(100)
                    status.update(label=f"今日数据刷新完成，用时 {elapsed:.1f} 秒。", state="complete")
                    _render_refresh_timing(load_state().get("refresh_timing", {}))
                    st.session_state["used_cache"] = False
                    st.session_state["last_refresh_notice"] = f"今日数据刷新完成，用时 {elapsed:.1f} 秒。"
                    st.session_state["show_refresh_timing"] = True
                    st.cache_data.clear()
                    st.rerun()
                else:
                    progress.progress(100)
                    status.update(label=f"刷新失败，用时 {elapsed:.1f} 秒，错误原因：{error}", state="error")
                    st.session_state["used_cache"] = False
                    st.error(f"刷新失败，用时 {elapsed:.1f} 秒，错误原因：{error}")

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


def _render_refresh_timing(timing: object) -> None:
    if not isinstance(timing, dict) or not timing:
        st.caption("暂无分阶段耗时。")
        return
    labels = {
        "total_seconds": "总耗时",
        "query_build_seconds": "生成搜索关键词",
        "tavily_fetch_seconds": "请求 Tavily",
        "filter_seconds": "清洗和过滤新闻",
        "candidate_build_seconds": "生成观察池",
        "news_summary_seconds": "生成新闻总结",
        "write_output_seconds": "写入缓存文件",
    }
    rows = []
    for key, label in labels.items():
        try:
            seconds = float(timing.get(key, 0) or 0)
        except (TypeError, ValueError):
            seconds = 0
        rows.append({"阶段": label, "耗时": f"{seconds:.1f} 秒"})
    st.table(rows)


def render_focus_table(rows: list[dict[str, str]]) -> None:
    st.subheader("今日重点观察股")
    focus = [row for row in rows if row.get("候选类型") in {"个股", "个股待补代码"} and row.get("观察建议") in POSITIVE_SUGGESTIONS]
    if not focus:
        render_empty_state("今日暂无观察股", "暂未识别到明确个股，题材观察和新闻总结仍可用于人工复盘。")
        return
    risky_count = sum(1 for row in focus if row.get("风险标签", "无") not in {"", "无"})
    if risky_count:
        st.warning(f"{risky_count} 条观察股带有风险标签，请先人工复核。")
    st.dataframe(_focus_display_rows(focus), use_container_width=True, hide_index=True)
    render_stock_details(focus)


def _focus_display_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    display_rows = []
    for row in _risk_display(rows):
        display_rows.append(
            {
                "股票名称": row.get("股票名称", ""),
                "股票代码": row.get("股票代码", ""),
                "所属题材": row.get("所属题材", ""),
                "可信度分数": row.get("可信度分数", ""),
                "观察建议": row.get("观察建议", ""),
                "风险标签": _compact_text(row.get("风险标签", ""), 40),
                "相关新闻标题": _compact_text(row.get("相关新闻标题", ""), 50),
                "发布时间": row.get("发布时间") or "时间未知",
            }
        )
    return _select_columns(display_rows, MAIN_COLUMNS)


def render_stock_details(rows: list[dict[str, str]]) -> None:
    st.subheader("个股详情")
    for row in rows:
        title = f"{row.get('股票名称', '未命名')} {row.get('股票代码', '')}｜{row.get('所属题材', '')}｜{row.get('观察建议', '')}"
        with st.expander(title, expanded=False):
            st.write(f"观察条件：{_dedupe_text(row.get('观察条件', ''))}")
            st.write(f"放弃条件：{_dedupe_text(row.get('放弃条件', ''))}")
            st.write(f"市场信号：{_dedupe_text(row.get('市场信号', ''))}")
            st.write(f"信息来源：{_dedupe_text(row.get('信息来源', ''))}")
            url = row.get("原始链接", "")
            if url:
                st.markdown(f"原始链接：[查看原文]({url})")
            else:
                st.write("原始链接：暂无")
            st.write(f"备注：{_dedupe_text(row.get('备注', ''))}")


def render_hot_news() -> None:
    st.subheader("今日热点新闻")
    rows = [
        row
        for row in load_csv_rows(SEARCH_DEDUPED_PATH)
        if row.get("是否保留") == "是" or row.get("结果类型") == "高质量新闻"
    ]
    if not rows:
        render_empty_state("暂无热点新闻", "当前缓存里没有保留新闻，请检查 Tavily Key、数据源状态或稍后刷新。")
        return
    rows.sort(key=lambda row: (_score({"可信度分数": row.get("A股相关性分数", "0")}) * -1, row.get("题材", "")))
    render_news_cards(rows[:12])
    display_rows = []
    for row in rows[:80]:
        display_rows.append(
            {
                "标题": _compact_text(row.get("标题", ""), 50),
                "来源": row.get("来源", ""),
                "题材": row.get("题材", ""),
                "发布时间_北京时间": row.get("发布时间_北京时间") or format_publish_time(row.get("发布时间", "")),
                "A股相关性分数": row.get("A股相关性分数", ""),
                "原始链接": row.get("原始链接", ""),
            }
        )
    with st.expander("新闻明细表", expanded=False):
        try:
            st.dataframe(
                display_rows,
                use_container_width=True,
                hide_index=True,
                column_config={"原始链接": st.column_config.LinkColumn("原始链接", display_text="查看原文")},
            )
        except Exception:
            st.dataframe(display_rows, use_container_width=True, hide_index=True)


def render_news_cards(rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    for index in range(0, len(rows), 2):
        cols = st.columns(2)
        for col, row in zip(cols, rows[index : index + 2]):
            with col:
                title = _compact_text(row.get("标题", ""), 62)
                source = row.get("来源", "") or "未知来源"
                topic = row.get("题材", "") or "未识别题材"
                publish_time = row.get("发布时间_北京时间") or format_publish_time(row.get("发布时间", ""))
                score = row.get("A股相关性分数", "")
                url = row.get("原始链接", "")
                link = f'<a class="card-link" href="{_escape_attr(url)}" target="_blank">查看原文</a>' if url else ""
                _render_html(
                    f"""
                    <div class="news-card">
                        <div class="news-title">{_escape(title)}</div>
                        <div class="radar-badge-row">
                            {_badge(source, "blue")}
                            {_badge(topic, "gray")}
                            {_badge(f"A股相关性 {score}", "green" if _score({"可信度分数": score}) >= 70 else "orange")}
                        </div>
                        <div class="news-meta">发布时间：{_escape(publish_time or "时间未知")}</div>
                        {link}
                    </div>
                    """
                )


def render_news_summary() -> None:
    st.subheader("今日热点新闻总结")
    rows = load_csv_rows(NEWS_SUMMARY_PATH)
    if not rows:
        st.info("暂无新闻总结。")
        return
    columns = ["题材", "今日催化", "核心新闻", "来源", "原始链接", "市场反应", "风险点", "总结等级"]
    st.dataframe(_select_columns(rows, columns), use_container_width=True, hide_index=True)


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
        columns = ["标题", "来源", "域名", "题材", "查询词", "结果类型", "A股相关性分数", "是否保留", "过滤原因"]
        st.dataframe(_select_columns(filtered, columns), use_container_width=True, hide_index=True)


def render_watchlist_management() -> pd.DataFrame:
    storage_mode = get_storage_mode(PROJECT_ROOT)
    if storage_mode == "google_sheets":
        st.warning(google_sheets_message())
        return pd.DataFrame(columns=WATCHLIST_FIELDS)
    warning = csv_cloud_warning(PROJECT_ROOT)
    if warning:
        st.warning(warning)

    current_df = load_watchlist_store(PROJECT_ROOT)
    _render_add_watchlist_form()
    st.markdown("### 编辑自选股表格")
    edited_df = st.data_editor(
        current_df,
        key="watchlist_editor",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "关注级别": st.column_config.SelectboxColumn("关注级别", options=LEVEL_OPTIONS),
            "持仓状态": st.column_config.SelectboxColumn("持仓状态", options=POSITION_OPTIONS),
            "成本价": st.column_config.TextColumn("成本价"),
            "备注": st.column_config.TextColumn("备注"),
        },
    )
    cols = st.columns([1, 1, 4])
    if cols[0].button("保存自选股列表", type="primary", use_container_width=True):
        result = save_watchlist(edited_df, PROJECT_ROOT)
        _handle_watchlist_store_result(result)
    _render_delete_watchlist_form(current_df)
    return load_watchlist_store(PROJECT_ROOT)


def _render_add_watchlist_form() -> None:
    st.markdown("### 添加自选股")
    with st.form("add_watchlist_stock", clear_on_submit=True):
        cols = st.columns([1.2, 1, 1.2, 0.8, 0.9, 0.8])
        name = cols[0].text_input("股票名称", placeholder="例如：比亚迪")
        code = cols[1].text_input("股票代码", placeholder="例如：002594", max_chars=6)
        topic = cols[2].text_input("所属题材", placeholder="例如：新能源/军工")
        level = cols[3].selectbox("关注级别", LEVEL_OPTIONS, index=1)
        position = cols[4].selectbox("持仓状态", POSITION_OPTIONS, index=1)
        cost = cols[5].text_input("成本价", placeholder="可留空")
        note = st.text_area("备注", placeholder="记录你关注的催化、公告或风险点", height=80)
        submitted = st.form_submit_button("添加 / 更新自选股", type="primary")
    if submitted:
        result = add_or_update_stock(
            {
                "股票名称": name,
                "股票代码": code,
                "所属题材": topic,
                "关注级别": level,
                "持仓状态": position,
                "成本价": cost,
                "备注": note,
            },
            PROJECT_ROOT,
        )
        _handle_watchlist_store_result(result)


def _render_delete_watchlist_form(current_df: pd.DataFrame) -> None:
    if current_df.empty:
        return
    options: dict[str, str] = {}
    for _, row in current_df.fillna("").astype(str).iterrows():
        code = row.get("股票代码", "").strip()
        name = row.get("股票名称", "").strip()
        if code:
            options[f"{name} {code}"] = code
    if not options:
        return
    with st.form("delete_watchlist_stock"):
        selected = st.selectbox("删除自选股", list(options.keys()))
        submitted = st.form_submit_button("删除选中自选股")
    if submitted:
        result = delete_stock(options[selected], PROJECT_ROOT)
        _handle_watchlist_store_result(result)


def _handle_watchlist_store_result(result: Any) -> None:
    if result.success:
        st.cache_data.clear()
        st.session_state["watchlist_save_notice"] = result.message
        st.rerun()
    for error in result.errors or [result.message]:
        st.error(error)


def render_watchlist_page(state: dict[str, Any]) -> None:
    st.subheader("我的自选股")
    notice = st.session_state.pop("watchlist_save_notice", "")
    if notice:
        st.success(notice)
        st.info("自选股已保存。点击“立即刷新今日数据”后才会检索新闻。")
    watchlist_df = load_watchlist_store(PROJECT_ROOT)
    watchlist_rows = _dataframe_rows(watchlist_df)
    review_rows = cached_csv_rows(WATCHLIST_REVIEW_PATH)
    news_rows = [row for row in cached_csv_rows(WATCHLIST_NEWS_PATH) if row.get("是否保留") == "是"]
    _render_watchlist_summary(watchlist_rows, review_rows, state)
    if not watchlist_rows:
        render_empty_state("暂无自选股情报", "暂无自选股，请在下方添加股票后保存。保存不会自动联网检索。")
        with st.expander("添加 / 编辑自选股", expanded=True):
            render_watchlist_management()
        return

    news_by_stock: dict[str, list[dict[str, str]]] = {}
    for row in news_rows:
        news_by_stock.setdefault(_watchlist_key(row), []).append(row)
    review_by_stock = {_watchlist_key(row): row for row in review_rows}

    render_watchlist_cards(watchlist_rows, review_by_stock, news_by_stock)

    with st.expander("添加 / 编辑自选股", expanded=False):
        render_watchlist_management()

    with st.expander("自选股明细表", expanded=False):
        st.dataframe(_watchlist_table_rows(watchlist_rows), use_container_width=True, hide_index=True)


def _render_watchlist_summary(watchlist_rows: list[dict[str, str]], review_rows: list[dict[str, str]], state: dict[str, Any]) -> None:
    render_metric_cards(
        [
            ("自选股数量", str(len(watchlist_rows)), "当前维护的股票", "W"),
            ("今日有新消息", str(len([row for row in review_rows if row.get("情报状态") in {"有新消息", "有风险", "仅有传闻"}])), "正式新闻、风险或传闻", "N"),
            ("风险提示数量", str(sum(_int_text(row.get("风险数量")) for row in review_rows)), "需人工复核", "R"),
            ("传闻数量", str(sum(_int_text(row.get("传闻数量")) for row in review_rows)), "未证实线索", "Q"),
            ("上次刷新时间", state.get("watchlist_last_update_time") or _latest_value([row.get("更新时间", "") for row in review_rows]) or "暂无", "自选股情报刷新", "T"),
            ("存储模式", get_storage_mode(PROJECT_ROOT), "当前自选股存储", "D"),
        ]
    )


def render_watchlist_cards(
    watchlist_rows: list[dict[str, str]],
    review_by_stock: dict[str, dict[str, str]],
    news_by_stock: dict[str, list[dict[str, str]]],
) -> None:
    for index in range(0, len(watchlist_rows), 2):
        cols = st.columns(2)
        for col, watch_row in zip(cols, watchlist_rows[index : index + 2]):
            review = review_by_stock.get(_watchlist_key(watch_row), {})
            stock_news = news_by_stock.get(_watchlist_key(watch_row), [])
            with col:
                _render_watchlist_card(watch_row, review)
                with st.expander("查看详细情报", expanded=False):
                    if not review:
                        st.info("已加入自选股，等待下次刷新生成情报。")
                    else:
                        st.write(f"核心理由：{review.get('核心理由', '')}")
                        st.write(f"观察条件：{review.get('观察条件', '')}")
                        st.write(f"放弃条件：{review.get('放弃条件', '')}")
                    if not stock_news:
                        st.info("暂无保留消息。")
                    for message_type in ["正式新闻", "公告信息", "社媒传闻", "风险消息", "行情异动"]:
                        _render_watchlist_news_group(message_type, stock_news)
                st.caption("编辑自选股：在下方“添加 / 编辑自选股”中修改。")


def _render_watchlist_card(watch_row: dict[str, str], review: dict[str, str]) -> None:
    name = watch_row.get("股票名称", "未命名")
    code = watch_row.get("股票代码", "")
    topic = watch_row.get("所属题材", "") or "未填写题材"
    level = watch_row.get("关注级别", "") or "未填写"
    position = watch_row.get("持仓状态", "") or "未填写"
    cost = watch_row.get("成本价", "") or "未填写"
    suggestion = review.get("规则观察建议", "等待刷新")
    latest = review.get("最新消息标题") or "已加入自选股，等待下次刷新生成情报。"
    risk_count = _int_text(review.get("风险数量", "0"))
    risk_badge = _badge("风险" if risk_count else "无风险", "red" if risk_count else "green")
    _render_html(
        f"""
        <div class="watch-card">
            <div class="watch-title">{_escape(name)} <span style="color:#6B7280;font-weight:700;">{_escape(code)}</span></div>
            <div class="radar-badge-row">
                {_badge(topic, "gray")}
                {_badge(f"{level}关注" if level in LEVEL_OPTIONS else level, "orange" if level == "高" else "gray")}
                {_badge(position, _position_badge_color(position))}
                {risk_badge}
            </div>
            <div class="watch-counts">
                <div class="watch-count"><strong>{_escape(review.get("新闻数量", "0"))}</strong>新闻</div>
                <div class="watch-count"><strong>{_escape(review.get("公告数量", "0"))}</strong>公告</div>
                <div class="watch-count"><strong>{_escape(review.get("传闻数量", "0"))}</strong>传闻</div>
                <div class="watch-count"><strong>{_escape(review.get("风险数量", "0"))}</strong>风险</div>
            </div>
            <div class="watch-meta">成本价：{_escape(cost)}</div>
            <div class="watch-meta">最新消息：{_escape(_compact_text(latest, 72))}</div>
            <div class="radar-badge-row">{_badge(suggestion, _suggestion_badge_color(suggestion))}</div>
        </div>
        """
    )


def _watchlist_table_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    columns = ["股票名称", "股票代码", "所属题材", "关注级别", "持仓状态", "成本价", "备注"]
    compact_rows = []
    for row in rows:
        item = {column: row.get(column, "") for column in columns}
        item["备注"] = _compact_text(item.get("备注", ""), 30)
        compact_rows.append(item)
    return compact_rows


def _render_watchlist_news_group(message_type: str, rows: list[dict[str, str]]) -> None:
    group = [row for row in rows if row.get("消息类型") == message_type]
    if not group:
        return
    st.markdown(f"**{message_type}**")
    display_rows = [
        {
            "标题": _compact_text(row.get("标题", ""), 60),
            "来源": row.get("来源", ""),
            "发布时间": row.get("发布时间_北京时间", ""),
            "风险标签": row.get("风险标签", ""),
            "原始链接": row.get("原始链接", ""),
        }
        for row in group[:10]
    ]
    try:
        st.dataframe(
            display_rows,
            use_container_width=True,
            hide_index=True,
            column_config={"原始链接": st.column_config.LinkColumn("原始链接", display_text="查看原文")},
        )
    except Exception:
        st.dataframe(display_rows, use_container_width=True, hide_index=True)


def _dataframe_rows(df: pd.DataFrame) -> list[dict[str, str]]:
    if df.empty:
        return []
    rows = df.fillna("").astype(str).to_dict(orient="records")
    return [{key: _sanitize_text(value) for key, value in row.items()} for row in rows]


def _watchlist_key(row: dict[str, str]) -> str:
    return row.get("股票代码") or row.get("股票名称", "")


def _int_text(value: object) -> int:
    try:
        return int(float(str(value or "0")))
    except ValueError:
        return 0


def _latest_value(values: list[str]) -> str:
    clean = [value for value in values if value]
    return max(clean) if clean else ""


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


def render_empty_state(title: str, body: str) -> None:
    _render_html(
        f"""
        <div class="radar-empty-card">
            <strong>{_escape(title)}</strong>
            <span>{_escape(body)}</span>
        </div>
        """
    )


def _badge(text: object, color: str = "gray") -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return f'<span class="radar-badge badge-{_escape_attr(color)}">{_escape(value)}</span>'


def _pill(text: object, color: str = "gray") -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return f'<span class="radar-pill pill-{_escape_attr(color)}">{_escape(value)}</span>'


def _position_badge_color(value: str) -> str:
    if value == "持有":
        return "blue"
    if value == "观察":
        return "gray"
    if value == "已卖出":
        return "gray"
    return "gray"


def _suggestion_badge_color(value: str) -> str:
    if value in {"优先跟踪", "继续持有"}:
        return "green"
    if value in {"等待确认", "等待回踩", "只看核心"}:
        return "orange"
    if value in {"降低关注", "暂不参与", "直接排除"}:
        return "red"
    return "gray"


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _escape_attr(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _compact_text(value: object, max_len: int = 40) -> str:
    text = _dedupe_text(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _dedupe_text(value: object) -> str:
    text = _sanitize_text(value)
    parts: list[str] = []
    for chunk in str(text).replace("\n", "；").replace("|", "；").split("；"):
        item = chunk.strip()
        if item and item != "无" and item not in parts:
            parts.append(item)
    return "；".join(parts) if parts else "无"


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
    sold_status_token = "__WATCHLIST_SOLD_STATUS__"
    value = value.replace("已卖出", sold_status_token)
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
    value = value.replace(sold_status_token, "已卖出")
    return value


def main() -> None:
    st.set_page_config(page_title="A股热点个股雷达", layout="wide")
    inject_global_css()
    st.markdown("<meta http-equiv='refresh' content='300'>", unsafe_allow_html=True)
    ensure_runtime_dirs()
    export_missing_env_from_runtime(PROJECT_ROOT)
    tavily_ready = has_tavily_key()
    if not tavily_ready:
        st.warning("当前未配置 Tavily API Key，无法联网搜索。")
    notice = st.session_state.pop("last_refresh_notice", "")
    if notice:
        st.success(notice)
    show_refresh_timing = bool(st.session_state.pop("show_refresh_timing", False))

    state = load_state()
    st.session_state.setdefault("used_cache", True)

    ensure_candidates()

    candidates = apply_sidebar_filters(load_csv_rows(CANDIDATES_PATH))
    all_candidates = sort_candidates(load_csv_rows(CANDIDATES_PATH))
    risk_rows = load_csv_rows(RISK_FLAGS_PATH)
    data_ready = today_data_generated(load_state())

    render_header(state, all_candidates, risk_rows, tavily_ready=tavily_ready, data_ready=data_ready)
    if show_refresh_timing:
        with st.expander("本次刷新耗时", expanded=True):
            _render_refresh_timing(state.get("refresh_timing", {}))
    render_actions()
    observe_tab, watchlist_tab, news_tab, theme_tab, risk_tab, status_tab = st.tabs(
        ["今日观察", "我的自选股", "热点新闻", "题材排行", "风险排除", "数据状态"]
    )
    with observe_tab:
        render_focus_table(candidates)
        render_theme_observation(candidates)
    with watchlist_tab:
        render_watchlist_page(load_state())
    with news_tab:
        render_hot_news()
        render_news_summary()
    with theme_tab:
        render_theme_rank(candidates)
        render_theme_observation(candidates)
    with risk_tab:
        render_risk_section(risk_rows)
        render_excluded(candidates)
        render_rumors(candidates)
        render_filtered_search_results()
        render_placeholder_debug(candidates)
    with status_tab:
        render_source_status(state, all_candidates)
        render_report()
        render_cloud_debug(tavily_ready)


if __name__ == "__main__":
    main()

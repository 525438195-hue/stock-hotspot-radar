"""Storage adapter for user-maintained watchlist records.

The current implementation stores data in data/watchlist.csv. The public
functions intentionally hide the backend so a Google Sheets adapter can be
added later without rewriting the Streamlit page.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from secrets_manager import load_dotenv_values
from watchlist_monitor import WATCHLIST_FIELDS, ensure_watchlist_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "watchlist.csv"
LEVEL_OPTIONS = ["高", "中", "低"]
POSITION_OPTIONS = ["持有", "观察", "已卖出"]


@dataclass
class StoreResult:
    success: bool
    message: str
    errors: list[str]
    action: str = ""


def load_watchlist(project_root: Path | None = None) -> pd.DataFrame:
    root = project_root or PROJECT_ROOT
    mode = get_storage_mode(root)
    if mode == "google_sheets":
        return _empty_dataframe()
    path = _csv_path(root)
    ensure_watchlist_template(path)
    return _read_csv_dataframe(path)


def save_watchlist(df: pd.DataFrame, project_root: Path | None = None) -> StoreResult:
    root = project_root or PROJECT_ROOT
    mode = get_storage_mode(root)
    if mode == "google_sheets":
        return StoreResult(False, google_sheets_message(), [google_sheets_message()])
    normalized, errors = normalize_watchlist_dataframe(df)
    if errors:
        return StoreResult(False, "自选股列表存在无效行，未保存。", errors)
    _write_csv_dataframe(_csv_path(root), normalized)
    return StoreResult(True, "自选股已保存。点击“立即刷新今日数据”后才会检索新闻。", [])


def add_or_update_stock(record: dict[str, Any], project_root: Path | None = None) -> StoreResult:
    root = project_root or PROJECT_ROOT
    mode = get_storage_mode(root)
    if mode == "google_sheets":
        return StoreResult(False, google_sheets_message(), [google_sheets_message()])
    df = load_watchlist(root)
    incoming = pd.DataFrame([{field: str(record.get(field, "") or "").strip() for field in WATCHLIST_FIELDS}])
    normalized, errors = normalize_watchlist_dataframe(incoming)
    if errors:
        return StoreResult(False, "自选股信息无效，未保存。", errors)
    stock = normalized.iloc[0].to_dict()
    code = stock["股票代码"]
    existing_codes = set(str(value).strip() for value in df.get("股票代码", pd.Series(dtype=str)).fillna("").tolist())
    action = "updated" if code in existing_codes else "added"
    if action == "updated":
        df = df[df["股票代码"].astype(str).str.strip() != code]
    combined = pd.concat([df, normalized], ignore_index=True)
    saved, save_errors = normalize_watchlist_dataframe(combined)
    if save_errors:
        return StoreResult(False, "自选股列表存在无效行，未保存。", save_errors)
    _write_csv_dataframe(_csv_path(root), saved)
    verb = "已更新" if action == "updated" else "已添加"
    return StoreResult(True, f"{verb}自选股：{stock['股票名称']} {stock['股票代码']}", [], action=action)


def delete_stock(stock_code: str, project_root: Path | None = None) -> StoreResult:
    root = project_root or PROJECT_ROOT
    mode = get_storage_mode(root)
    if mode == "google_sheets":
        return StoreResult(False, google_sheets_message(), [google_sheets_message()])
    code = str(stock_code or "").strip()
    if not _valid_stock_code(code):
        return StoreResult(False, "股票代码必须是 6 位数字。", ["股票代码必须是 6 位数字。"])
    df = load_watchlist(root)
    before = len(df)
    df = df[df["股票代码"].astype(str).str.strip() != code]
    if len(df) == before:
        return StoreResult(False, f"未找到股票代码：{code}", [f"未找到股票代码：{code}"])
    _write_csv_dataframe(_csv_path(root), df)
    return StoreResult(True, f"已删除自选股：{code}", [], action="deleted")


def normalize_watchlist_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    if df is None:
        df = _empty_dataframe()
    working = df.copy()
    for field in WATCHLIST_FIELDS:
        if field not in working.columns:
            working[field] = ""
    working = working[WATCHLIST_FIELDS].fillna("").astype(str)
    errors: list[str] = []
    normalized_rows: list[dict[str, str]] = []
    by_code: dict[str, dict[str, str]] = {}
    for index, row in working.iterrows():
        record = {field: str(row.get(field, "") or "").strip() for field in WATCHLIST_FIELDS}
        if not any(record.values()):
            continue
        row_errors = validate_watchlist_record(record, row_number=int(index) + 1)
        if row_errors:
            errors.extend(row_errors)
            continue
        by_code[record["股票代码"]] = record
    if errors:
        return _empty_dataframe(), errors
    normalized_rows = list(by_code.values())
    return pd.DataFrame(normalized_rows, columns=WATCHLIST_FIELDS), []


def validate_watchlist_record(record: dict[str, Any], row_number: int | None = None) -> list[str]:
    prefix = f"第 {row_number} 行：" if row_number is not None else ""
    name = str(record.get("股票名称", "") or "").strip()
    code = str(record.get("股票代码", "") or "").strip()
    level = str(record.get("关注级别", "") or "").strip()
    position = str(record.get("持仓状态", "") or "").strip()
    cost = str(record.get("成本价", "") or "").strip()
    errors: list[str] = []
    if not name:
        errors.append(f"{prefix}股票名称必填。")
    if not _valid_stock_code(code):
        errors.append(f"{prefix}股票代码必须是 6 位数字。")
    if level and level not in LEVEL_OPTIONS:
        errors.append(f"{prefix}关注级别只能是：高 / 中 / 低。")
    if position and position not in POSITION_OPTIONS:
        errors.append(f"{prefix}持仓状态只能是：持有 / 观察 / 已卖出。")
    if cost and not _is_number(cost):
        errors.append(f"{prefix}成本价必须是数字或留空。")
    return errors


def get_storage_mode(project_root: Path | None = None) -> str:
    root = project_root or PROJECT_ROOT
    value = os.environ.get("WATCHLIST_STORAGE", "").strip()
    if not value:
        value = _streamlit_secret("WATCHLIST_STORAGE")
    if not value:
        value = str(load_dotenv_values(root).get("WATCHLIST_STORAGE", "")).strip()
    mode = value.lower() or "csv"
    if mode not in {"csv", "google_sheets"}:
        return "csv"
    return mode


def google_sheets_message() -> str:
    return "Google Sheets 存储尚未配置，请先使用 CSV 模式。"


def is_probably_streamlit_cloud() -> bool:
    return bool(os.environ.get("STREAMLIT_CLOUD") or os.environ.get("STREAMLIT_SHARING") or Path("/mount/src").exists())


def csv_cloud_warning(project_root: Path | None = None) -> str:
    if get_storage_mode(project_root) == "csv" and is_probably_streamlit_cloud():
        return "当前使用 CSV 存储。Streamlit Cloud 不保证本地文件持久化，重启后网页添加的自选股可能丢失。建议后续切换到 Google Sheets 存储。"
    return ""


def _csv_path(project_root: Path) -> Path:
    return project_root / "data" / "watchlist.csv"


def _empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=WATCHLIST_FIELDS)


def _read_csv_dataframe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_dataframe()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return pd.DataFrame(rows, columns=WATCHLIST_FIELDS).fillna("").astype(str)


def _write_csv_dataframe(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = df.copy()
    for field in WATCHLIST_FIELDS:
        if field not in normalized.columns:
            normalized[field] = ""
    normalized = normalized[WATCHLIST_FIELDS].fillna("").astype(str)
    normalized.to_csv(path, index=False, encoding="utf-8-sig")


def _valid_stock_code(value: str) -> bool:
    return value.isdigit() and len(value) == 6


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _streamlit_secret(key: str) -> str:
    try:
        import streamlit as st  # type: ignore

        return str(st.secrets.get(key, "") or "").strip()
    except Exception:
        return ""

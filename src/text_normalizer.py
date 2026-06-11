"""Text normalization helpers for Simplified Chinese output."""

from __future__ import annotations

from typing import Any


TEXT_FIELDS = {
    "title",
    "snippet",
    "content",
    "source",
    "query",
    "note",
    "remark",
    "标题",
    "摘要",
    "正文",
    "来源",
    "查询词",
    "备注",
    "相关关键词",
    "公告标题",
    "公告正文",
    "公司名称",
    "板块名称",
    "领涨股票",
}

_OPENCC = None
_OPENCC_ERROR = ""
_OPENCC_LOADED = False


def normalize_text(value: object) -> str:
    text = str(value or "")
    converter = _converter()
    if converter is None:
        return text
    return converter.convert(text)


def normalize_text_with_flag(value: object) -> tuple[str, bool]:
    text = str(value or "")
    normalized = normalize_text(text)
    return normalized, normalized != text


def normalize_record(record: dict[str, Any], fields: set[str] | None = None) -> tuple[dict[str, Any], bool]:
    target_fields = fields or TEXT_FIELDS
    changed = False
    normalized: dict[str, Any] = {}
    for key, value in record.items():
        if key in target_fields and isinstance(value, str):
            normalized_value, field_changed = normalize_text_with_flag(value)
            normalized[key] = normalized_value
            changed = changed or field_changed
        else:
            normalized[key] = value
    return normalized, changed


def normalize_records(records: list[dict[str, Any]], fields: set[str] | None = None) -> tuple[list[dict[str, Any]], bool]:
    changed = False
    normalized_records = []
    for record in records:
        normalized, record_changed = normalize_record(record, fields)
        normalized_records.append(normalized)
        changed = changed or record_changed
    return normalized_records, changed


def normalizer_warning() -> str:
    if _converter() is None:
        return f"opencc 未安装或初始化失败，已跳过繁简转换：{_OPENCC_ERROR or '未找到 opencc'}"
    return ""


def opencc_available() -> bool:
    return _converter() is not None


def _converter() -> Any | None:
    global _OPENCC, _OPENCC_ERROR, _OPENCC_LOADED
    if _OPENCC_LOADED:
        return _OPENCC
    _OPENCC_LOADED = True
    try:
        from opencc import OpenCC  # type: ignore

        _OPENCC = OpenCC("t2s")
        _OPENCC_ERROR = ""
    except Exception as exc:
        _OPENCC = None
        _OPENCC_ERROR = str(exc)
    return _OPENCC

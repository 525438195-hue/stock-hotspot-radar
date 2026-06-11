"""Runtime secret loading for local CLI and Streamlit Cloud."""

from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_SECRET_KEYS = [
    "TAVILY_API_KEY",
    "DEEPSEEK_API_KEY",
    "QWEN_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_CSE_ID",
    "NEWSAPI_KEY",
]


def load_dotenv_values(project_root: Path) -> dict[str, str]:
    path = project_root / ".env"
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values  # type: ignore
    except Exception:
        return {}
    values = dotenv_values(path)
    return {str(key): str(value or "").strip() for key, value in values.items() if key}


def load_streamlit_secrets() -> dict[str, str]:
    try:
        import streamlit as st  # type: ignore

        secrets = getattr(st, "secrets", {})
        result: dict[str, str] = {}
        for key in SUPPORTED_SECRET_KEYS:
            try:
                value = secrets.get(key, "")
            except Exception:
                value = ""
            if value:
                result[key] = str(value).strip()
        return result
    except Exception:
        return {}


def runtime_secret(project_root: Path, key: str, dotenv_values: dict[str, str] | None = None) -> str:
    env_value = os.environ.get(key, "").strip()
    if env_value:
        return env_value
    streamlit_value = load_streamlit_secrets().get(key, "").strip()
    if streamlit_value:
        return streamlit_value
    return str((dotenv_values or load_dotenv_values(project_root)).get(key, "")).strip()


def runtime_secrets(project_root: Path) -> dict[str, str]:
    dotenv_values = load_dotenv_values(project_root)
    return {key: runtime_secret(project_root, key, dotenv_values) for key in SUPPORTED_SECRET_KEYS}


def export_missing_env_from_runtime(project_root: Path) -> dict[str, str]:
    values = runtime_secrets(project_root)
    for key, value in values.items():
        if value and not os.environ.get(key):
            os.environ[key] = value
    return values

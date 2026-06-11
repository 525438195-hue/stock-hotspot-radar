"""Keyword based topic classification."""

from __future__ import annotations

from typing import Any


TOPIC_KEYWORDS: dict[str, list[str]] = {
    "低空经济": ["低空", "空域", "通航", "起降点", "管制系统", "通信导航"],
    "AI算力": ["AI", "算力", "服务器", "数据中心", "大模型", "GPU"],
    "机器人": ["机器人", "人形", "减速器", "关节模组"],
    "半导体": ["半导体", "芯片", "光刻", "刻蚀", "晶圆", "检测"],
    "新能源": ["新能源", "储能", "锂电", "光伏", "电池"],
    "医药": ["医药", "创新药", "并购", "生物"],
    "数据要素": ["数据要素", "公共数据", "数据流通", "授权运营"],
}


def classify_topic(event: dict[str, Any]) -> str:
    hint = str(event.get("topic_hint", "")).strip()
    if hint:
        return hint

    text = f"{event.get('title', '')}\n{event.get('content', '')}"
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return topic
    return "综合热点"

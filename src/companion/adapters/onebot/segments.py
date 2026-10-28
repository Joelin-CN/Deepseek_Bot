"""OneBot v11 message segment construction."""

from __future__ import annotations

from typing import Any


def text_segment(text: str) -> list[dict[str, Any]]:
    """构造结构化文本消息段（OneBot v11 数组格式）。"""
    return [{"type": "text", "data": {"text": text}}]

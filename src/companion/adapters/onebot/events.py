"""Parse OneBot v11 event payloads into internal models."""

from __future__ import annotations

import logging
from typing import Any

from companion.core.events import InboundMessage

logger = logging.getLogger(__name__)


def extract_text(message: Any) -> str:
    """从 message 字段提取纯文本（段数组或字符串两种格式都接受）。"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(
            segment.get("data", {}).get("text", "")
            for segment in message
            if isinstance(segment, dict) and segment.get("type") == "text"
        )
    return ""


def parse_private_message(raw: dict[str, Any]) -> InboundMessage | None:
    """把私聊消息事件转成 InboundMessage；缺失关键字段时返回 None。"""
    try:
        return InboundMessage(
            message_id=int(raw["message_id"]),
            user_id=int(raw["user_id"]),
            self_id=int(raw["self_id"]),
            sub_type=str(raw.get("sub_type", "")),
            text=extract_text(raw.get("message")),
            time=int(raw.get("time", 0)),
        )
    except (KeyError, TypeError, ValueError):
        logger.debug("丢弃无法解析的事件: %s", str(raw)[:200])
        return None

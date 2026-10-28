"""Parse OneBot v11 event payloads into internal models.

同时支持私聊与群聊消息事件；平台细节（字段名、消息段格式）只留在这
一层，内核使用 core.events 的统一模型。
"""

from __future__ import annotations

import logging
from typing import Any

from companion.core.events import InboundMessage
from companion.core.sessions.identity import SessionKey

logger = logging.getLogger(__name__)

PLATFORM = "qq"


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


def parse_message(raw: dict[str, Any]) -> InboundMessage | None:
    """把 OneBot 消息事件转成 InboundMessage；缺失关键字段时返回 None。"""
    try:
        message_type = str(raw["message_type"])
        self_id = int(raw["self_id"])
        user_id = int(raw["user_id"])
        if message_type == "private":
            chat_target = user_id
        elif message_type == "group":
            chat_target = int(raw["group_id"])
        else:
            return None
        session = SessionKey(
            platform=PLATFORM, bot_id=self_id, chat_type=message_type, chat_target=chat_target
        )
        sender = raw.get("sender") or {}
        return InboundMessage(
            message_id=int(raw["message_id"]),
            session=session,
            sender_id=user_id,
            sender_nickname=str(sender.get("card") or sender.get("nickname") or ""),
            sub_type=str(raw.get("sub_type", "")),
            text=extract_text(raw.get("message")),
            time=int(raw.get("time", 0)),
        )
    except (KeyError, TypeError, ValueError):
        logger.debug("丢弃无法解析的事件: %s", str(raw)[:200])
        return None

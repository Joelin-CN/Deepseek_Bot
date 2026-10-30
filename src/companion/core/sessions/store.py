"""消息状态与用量落库（业务规则层，基于 persistence 通用层）。

messages 表的一条记录对应一条入站消息的处理链：
received → model_generated → sent_ok / send_failed / send_unknown /
consumed。send_unknown 表示消息可能已送达但回执未知——此时不自动重
发；consumed 表示被插件消费且无需回复。
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from companion.core.events import InboundMessage
from companion.persistence import Database

logger = logging.getLogger(__name__)

DeliveryStatus = Literal["sent_ok", "send_failed", "send_unknown", "consumed"]


class MessageStore:
    def __init__(self, db: Database):
        self._db = db

    async def record_inbound(self, message: InboundMessage) -> int:
        now = time.time()
        return await self._db.execute(
            """
            INSERT INTO messages
                (ts, platform, bot_id, chat_type, chat_target, sender_id, session_key,
                 platform_message_id, status, content_preview, content_len, content, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?, ?)
            """,
            (
                message.time or now,
                message.session.platform,
                message.session.bot_id,
                message.session.chat_type,
                message.session.chat_target,
                message.sender_id,
                message.session.as_scope(),
                message.message_id,
                message.text[:120],
                len(message.text),
                message.text,
                now,
            ),
        )

    async def mark_generated(
        self, row_id: int, reply_text: str, *, conversational: bool = True
    ) -> None:
        """记录生成结果；conversational=False（如命令回执）不写入对话全文列。"""
        await self._db.execute(
            "UPDATE messages SET status='model_generated', reply_preview=?, reply_content=?, updated_ts=? WHERE id=?",
            (reply_text[:120], reply_text if conversational else "", time.time(), row_id),
        )

    async def mark_delivery(
        self, row_id: int, status: DeliveryStatus, delivery_message_id: int | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE messages SET status=?, delivery_message_id=?, updated_ts=? WHERE id=?",
            (status, delivery_message_id, time.time(), row_id),
        )

    async def get(self, row_id: int) -> dict | None:
        row = await self._db.fetchone(
            "SELECT id, ts, session_key, platform_message_id, status, content_preview, "
            "reply_preview, delivery_message_id FROM messages WHERE id=?",
            (row_id,),
        )
        if row is None:
            return None
        keys = (
            "id", "ts", "session_key", "platform_message_id", "status",
            "content_preview", "reply_preview", "delivery_message_id",
        )
        return dict(zip(keys, row))

    async def delivered_turns(self, session_key: str, limit: int) -> list[tuple[str, str]]:
        """取该会话最近 limit 轮已完整送达的对话（新→旧），用于恢复有界历史。

        只有 sent_ok 且双方全文都在的记录构成一轮；发送失败、结果未知
        或命令回执不算对话历史。
        """
        if limit <= 0:
            return []
        rows = await self._db.fetchall(
            "SELECT content, reply_content FROM messages "
            "WHERE session_key=? AND status='sent_ok' AND content<>'' AND reply_content<>'' "
            "ORDER BY id DESC LIMIT ?",
            (session_key, limit),
        )
        return [(row[0], row[1]) for row in rows]


class UsageStore:
    """模型用量逐次落库；不记录密钥与完整对话内容。"""

    def __init__(self, db: Database):
        self._db = db

    async def record(
        self,
        *,
        session_key: str,
        actor_id: int,
        source: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO usage
                (ts, session_key, actor_id, source, model,
                 prompt_tokens, completion_tokens, total_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(), session_key, actor_id, source, model,
                prompt_tokens, completion_tokens, total_tokens,
            ),
        )

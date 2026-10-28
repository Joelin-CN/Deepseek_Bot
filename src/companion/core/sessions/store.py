"""消息状态与用量落库（业务规则层，基于 persistence 通用层）。

messages 表的一条记录对应一条入站消息的处理链：
received → model_generated → sent_ok / send_failed / send_unknown。
send_unknown 表示消息可能已送达但回执未知——此时不自动重发。
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from companion.core.events import InboundMessage
from companion.persistence import Database

logger = logging.getLogger(__name__)

DeliveryStatus = Literal["sent_ok", "send_failed", "send_unknown"]


class MessageStore:
    def __init__(self, db: Database):
        self._db = db

    async def record_inbound(self, message: InboundMessage) -> int:
        now = time.time()
        return await self._db.execute(
            """
            INSERT INTO messages
                (ts, platform, bot_id, chat_type, chat_target, sender_id, session_key,
                 platform_message_id, status, content_preview, content_len, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?)
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
                now,
            ),
        )

    async def mark_generated(self, row_id: int, reply_text: str) -> None:
        await self._db.execute(
            "UPDATE messages SET status='model_generated', reply_preview=?, updated_ts=? WHERE id=?",
            (reply_text[:120], time.time(), row_id),
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

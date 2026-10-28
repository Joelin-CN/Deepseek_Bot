"""Internal message model.

The kernel works only with these shapes; OneBot raw payloads stay inside the
``companion.adapters.onebot`` package and never leak into core modules.
"""

from __future__ import annotations

from dataclasses import dataclass

from companion.core.sessions.identity import SessionKey


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """解析后的入站消息（筛选通过前的内部表示）。"""

    message_id: int
    session: SessionKey
    sender_id: int  # 群聊为群内实际发送者；私聊等于 chat_target
    sender_nickname: str
    sub_type: str
    text: str
    time: int

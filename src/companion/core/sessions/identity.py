"""统一会话标识。

所有按会话组织的能力——短期上下文、并发锁、消息去重、记忆范围、
消息记录——都使用 SessionKey，保证群聊接入后各处标识规则一致。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class SessionKey:
    """一条会话的唯一标识：平台 + 机器人账号 + 会话类型 + 会话目标。

    私聊 chat_target 为对方 QQ 号；群聊 chat_target 为群号，群内具体
    发送者记录在消息的 sender_id 上，不参与会话标识。
    """

    platform: str  # "qq"
    bot_id: int
    chat_type: str  # "private" | "group"
    chat_target: int

    def as_scope(self) -> str:
        """用作持久化与记忆范围的规范字符串。"""
        return f"{self.platform}/{self.bot_id}/{self.chat_type}/{self.chat_target}"

    def __str__(self) -> str:  # pragma: no cover - 展示用途
        return self.as_scope()

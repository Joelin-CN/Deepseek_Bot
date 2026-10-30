"""运行上下文：发起者身份、来源会话、任务标识、允许能力与取消信号。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet

from companion.core.sessions.identity import SessionKey


class Role(str, Enum):
    OWNER = "owner"  # 本地配置的主人
    MEMBER = "member"  # 授权私聊发送者 / 白名单成员
    UNKNOWN = "unknown"  # 未授权来源


class Capability(str, Enum):
    CHAT = "chat"  # 触发模型对话
    MEMORY_READ = "memory_read"  # 查看本会话记忆
    MEMORY_MANAGE = "memory_manage"  # 增改删本会话记忆
    ADMIN_STATUS = "admin_status"  # 管理命令（M9 使用）
    WEB_BROWSE = "web_browse"  # 通过 ego-lite 浏览公开网页（M5 起仅主人）


ROLE_CAPABILITIES: dict[Role, FrozenSet[Capability]] = {
    Role.OWNER: frozenset(Capability),
    Role.MEMBER: frozenset({Capability.CHAT, Capability.MEMORY_READ, Capability.MEMORY_MANAGE}),
    Role.UNKNOWN: frozenset(),
}


class PermissionDenied(Exception):
    """当前身份不具备所需能力。"""


@dataclass(frozen=True, slots=True)
class Actor:
    platform: str
    user_id: int
    role: Role
    display_name: str = ""

    @property
    def capabilities(self) -> FrozenSet[Capability]:
        return ROLE_CAPABILITIES[self.role]


def actor_for(
    platform: str,
    sender_id: int,
    nickname: str,
    *,
    owner_id: int | None,
    allowed_senders: set[int],
) -> Actor:
    """由已验证的平台发送者 ID 与本地配置推导身份。"""
    if owner_id is not None and sender_id == owner_id:
        role = Role.OWNER
    elif sender_id in allowed_senders:
        role = Role.MEMBER
    else:
        role = Role.UNKNOWN
    return Actor(platform=platform, user_id=sender_id, role=role, display_name=nickname)


@dataclass(frozen=True, slots=True)
class RunContext:
    """一次消息处理/任务的执行上下文。"""

    session: SessionKey
    actor: Actor
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    @property
    def capabilities(self) -> FrozenSet[Capability]:
        return self.actor.capabilities

    def require(self, capability: Capability) -> None:
        if capability not in self.actor.capabilities:
            raise PermissionDenied(
                f"{self.actor.platform} 用户 {self.actor.user_id}（{self.actor.role.value}）"
                f"不具备能力 {capability.value}"
            )


def new_run_context(session: SessionKey, actor: Actor) -> RunContext:
    return RunContext(session=session, actor=actor)

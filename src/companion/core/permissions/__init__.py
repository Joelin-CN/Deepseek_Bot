"""身份、能力与运行上下文。

身份和权限只来自已验证的消息来源（平台账号 ID）与本地配置；不接受
模型参数或聊天文字自行声明身份。会话隔离（SessionKey）与身份授权
（Actor/Role）分别处理：进同一群不代表获得任何管理能力。
"""

from companion.core.permissions.context import (
    Actor,
    Capability,
    PermissionDenied,
    Role,
    RunContext,
    actor_for,
)

__all__ = [
    "Actor",
    "Capability",
    "PermissionDenied",
    "Role",
    "RunContext",
    "actor_for",
]

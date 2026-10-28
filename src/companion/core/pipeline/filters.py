"""入站事件筛选：身份与来源检查。

只做纯检查，不做 I/O；去重由会话管理器负责。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class FilterChain:
    def __init__(
        self,
        self_id: int,
        allowed_senders: set[int],
        allow_sub_types: frozenset[str] = frozenset({"friend"}),
    ):
        self._self_id = self_id
        self._allowed = allowed_senders
        self._sub_types = allow_sub_types

    def classify(self, raw: dict[str, Any]) -> tuple[bool, str]:
        """返回 (是否处理, 原因)；原因为 'accept' 或拒绝说明。"""
        post_type = raw.get("post_type")
        if post_type != "message":
            return False, f"post_type={post_type!r}"
        message_type = raw.get("message_type")
        if message_type != "private":
            return False, f"message_type={message_type!r}"
        self_id = raw.get("self_id")
        if self_id != self._self_id:
            return False, f"self_id={self_id} 与配置的机器人 {self._self_id} 不符"
        user_id = raw.get("user_id")
        if user_id == self._self_id:
            return False, "自身消息"
        if user_id not in self._allowed:
            return False, f"发送者 {user_id} 不在授权列表"
        sub_type = raw.get("sub_type")
        if sub_type not in self._sub_types:
            return False, f"sub_type={sub_type!r}"
        return True, "accept"

    def accepts(self, raw: dict[str, Any]) -> bool:
        accepted, reason = self.classify(raw)
        if not accepted:
            logger.debug("过滤事件（%s）: %s", reason, str(raw)[:200])
        return accepted

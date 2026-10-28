"""会话管理：按统一会话标识组织上下文、并发锁与消息去重。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Hashable

if TYPE_CHECKING:  # 避免与 core.events 的运行时循环导入
    from companion.core.events import InboundMessage, SessionKey


class DedupCache:
    """有界 LRU 的已处理消息键集合。"""

    def __init__(self, maxsize: int = 2048):
        self._maxsize = maxsize
        self._seen: OrderedDict[Hashable, None] = OrderedDict()

    def seen_before(self, key: Hashable) -> bool:
        """记录该键；若之前已记录过则返回 True（重复消息）。"""
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        if len(self._seen) > self._maxsize:
            self._seen.popitem(last=False)
        return False


class SessionLocks:
    """按 key 的 asyncio 锁：同一会话串行，不同会话并发。"""

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def for_key(self, key: Hashable) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


class SessionManager:
    """上下文、并发锁与去重都使用 SessionKey，保证群聊接入后规则一致。"""

    def __init__(self, dedup_size: int = 2048):
        self._dedup = DedupCache(dedup_size)
        self._locks = SessionLocks()
        self._histories: dict[SessionKey, deque[dict[str, str]]] = {}

    def seen(self, message: InboundMessage) -> bool:
        """消息去重键 = 机器人账号 + 平台 message_id。"""
        return self._dedup.seen_before((message.session.bot_id, message.message_id))

    def history(self, session: SessionKey) -> deque[dict[str, str]]:
        history = self._histories.get(session)
        if history is None:
            history = deque()
            self._histories[session] = history
        return history

    def lock(self, session: SessionKey) -> asyncio.Lock:
        return self._locks.for_key(session)

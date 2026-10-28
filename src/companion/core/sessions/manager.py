"""消息去重与同会话执行串行化。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict


class DedupCache:
    """有界 LRU 的已处理 message_id 集合。"""

    def __init__(self, maxsize: int = 2048):
        self._maxsize = maxsize
        self._seen: OrderedDict[int, None] = OrderedDict()

    def seen_before(self, message_id: int) -> bool:
        """记录该 id；若之前已记录过则返回 True（重复消息）。"""
        if message_id in self._seen:
            self._seen.move_to_end(message_id)
            return True
        self._seen[message_id] = None
        if len(self._seen) > self._maxsize:
            self._seen.popitem(last=False)
        return False


class SessionLocks:
    """按 key 的 asyncio 锁：同一会话串行，不同会话并发。"""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def for_key(self, key: int) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

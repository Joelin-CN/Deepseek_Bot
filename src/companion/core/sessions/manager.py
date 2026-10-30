"""会话管理：按统一会话标识组织上下文、并发锁与消息去重。

内存中的短期历史本身有界（按消息条数，deque maxlen），不只裁剪请求
副本；历史可由持久化的已送达轮次按会话懒加载恢复（每进程一次）。
清空（如忘记记忆后）只影响内存，不会再次从库中拉回旧轮次。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from typing import TYPE_CHECKING, Awaitable, Callable, Hashable

if TYPE_CHECKING:  # 避免与 core.events 的运行时循环导入
    from companion.core.events import InboundMessage, SessionKey

HistoryLoader = Callable[["SessionKey", int], Awaitable[list[dict[str, str]]]]


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

    def __init__(self, dedup_size: int = 2048, max_stored_messages: int = 40):
        self._dedup = DedupCache(dedup_size)
        self._locks = SessionLocks()
        self._max_stored = max_stored_messages
        self._histories: dict["SessionKey", deque[dict[str, str]]] = {}
        self._loaded: set["SessionKey"] = set()
        self._loader: HistoryLoader | None = None

    def set_history_loader(self, loader: HistoryLoader) -> None:
        """设置按会话恢复已送达历史的加载器（来自 MessageStore）。"""
        self._loader = loader

    def seen(self, message: "InboundMessage") -> bool:
        """消息去重键 = 机器人账号 + 平台 message_id。"""
        return self._dedup.seen_before((message.session.bot_id, message.message_id))

    async def history(self, session: "SessionKey") -> deque[dict[str, str]]:
        """取该会话的内存历史；首次访问时从持久层恢复（每进程一次）。"""
        history = self._histories.get(session)
        if history is None:
            history = deque(maxlen=self._max_stored) if self._max_stored > 0 else deque(maxlen=0)
            self._histories[session] = history
        if session not in self._loaded:
            self._loaded.add(session)
            if self._loader is not None and self._max_stored > 0:
                turns = await self._loader(session, self._max_stored)
                history.extend(turns)  # deque maxlen 自动丢弃最旧的溢出
        return history

    def append_turn(self, session: "SessionKey", user_text: str, assistant_text: str) -> None:
        """记录一轮已完整送达的对话；内存上限由 deque maxlen 强制。"""
        history = self._histories.get(session)
        if history is None:
            history = deque(maxlen=self._max_stored) if self._max_stored > 0 else deque(maxlen=0)
            self._histories[session] = history
        if self._max_stored <= 0:
            return
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})

    def clear_history(self, session: "SessionKey") -> None:
        """清空该会话的内存短期上下文；标记已加载，不会再从库中拉回。"""
        self._histories[session] = deque(maxlen=self._max_stored) if self._max_stored > 0 else deque(maxlen=0)
        self._loaded.add(session)

    def lock(self, session: "SessionKey") -> asyncio.Lock:
        return self._locks.for_key(session)

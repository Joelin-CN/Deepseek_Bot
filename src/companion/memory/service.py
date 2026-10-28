"""记忆服务：权限与范围强制层。

所有操作的 scope 一律取自当前 RunContext 的会话标识——私聊记忆只用
于对应私聊、群与群之间互相隔离，跨范围访问在服务层不可表达。删除
或修订后，active 检索、上下文注入即时反映（上下文无长驻缓存）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from companion.core.events import SessionKey
from companion.core.permissions import (
    Capability,
    PermissionDenied,
    RunContext,
)
from companion.memory.store import MemoryRecord, MemoryStore, PAGE_DEFAULT_SIZE

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from companion.persistence import Database


class MemoryService:
    def __init__(self, store: MemoryStore):
        self._store = store

    @classmethod
    def from_database(cls, db: "Database") -> "MemoryService":
        from companion.memory.store import MemoryStore

        return cls(MemoryStore(db))

    async def remember(self, ctx: RunContext, content: str, source_message_id: int | None = None) -> int:
        ctx.require(Capability.MEMORY_MANAGE)
        memory_id = await self._store.add(ctx.session.as_scope(), content, source_message_id)
        logger.info(
            "新增记忆 #%d: session=%s sender=%s", memory_id, ctx.session.as_scope(), ctx.actor.user_id
        )
        return memory_id

    async def forget(self, ctx: RunContext, memory_id: int) -> bool:
        ctx.require(Capability.MEMORY_MANAGE)
        ok = await self._store.soft_delete(memory_id, ctx.session.as_scope())
        if ok:
            logger.info("删除记忆 #%d: session=%s sender=%s", memory_id, ctx.session.as_scope(), ctx.actor.user_id)
        return ok

    async def revise(self, ctx: RunContext, memory_id: int, new_content: str) -> MemoryRecord | None:
        ctx.require(Capability.MEMORY_MANAGE)
        scope = ctx.session.as_scope()
        new_id = await self._store.revise(memory_id, scope, new_content)
        if new_id is None:
            return None
        logger.info(
            "修订记忆 #%d → #%d: session=%s sender=%s",
            memory_id, new_id, scope, ctx.actor.user_id,
        )
        record = await self._store.get(new_id, scope)
        assert record is not None
        return record

    async def list_memories(
        self, ctx: RunContext, page: int = 1, page_size: int = PAGE_DEFAULT_SIZE
    ) -> tuple[list[MemoryRecord], int]:
        ctx.require(Capability.MEMORY_READ)
        return await self._store.list_page(ctx.session.as_scope(), page, page_size)

    async def search(
        self, ctx: RunContext, query: str, limit: int = 5
    ) -> tuple[list[MemoryRecord], int]:
        ctx.require(Capability.MEMORY_READ)
        return await self._store.search(ctx.session.as_scope(), query, limit=limit)

    async def context_slice(self, session: SessionKey, max_items: int) -> list[str]:
        """按会话取最近有效记忆用于模型上下文；只在本会话管道内调用。"""
        if max_items <= 0:
            return []
        records = await self._store.active_recent(session.as_scope(), max_items)
        return [r.content for r in records]

"""长期记忆存储（SQL 层）：范围、修订链与软删除。

检索只返回 active；修订产生新行并以 revision_of 链接旧行；删除为软
删除（保留修订历史与来源可追溯性）。中文检索使用 LIKE 子串匹配，
对显式记忆的量级正确且不依赖分词；FTS5/向量检索作为后续扩展点。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from companion.persistence import Database

logger = logging.getLogger(__name__)

PAGE_DEFAULT_SIZE = 5


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    scope: str
    content: str
    status: str  # active / superseded / deleted
    source_message_id: int | None
    revision_of: int | None
    created_ts: float
    updated_ts: float

    @property
    def preview(self) -> str:
        return self.content if len(self.content) <= 50 else self.content[:50] + "…"


def _like_escape(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_COLUMNS = (
    "id, scope, content, status, source_message_id, revision_of, created_ts, updated_ts"
)


def _row_to_record(row: tuple) -> MemoryRecord:
    return MemoryRecord(
        id=row[0], scope=row[1], content=row[2], status=row[3],
        source_message_id=row[4], revision_of=row[5], created_ts=row[6], updated_ts=row[7],
    )


class MemoryStore:
    def __init__(self, db: Database):
        self._db = db

    async def add(self, scope: str, content: str, source_message_id: int | None = None) -> int:
        now = time.time()
        return await self._db.execute(
            "INSERT INTO memories (scope, content, status, source_message_id, revision_of, "
            "created_ts, updated_ts) VALUES (?, ?, 'active', ?, NULL, ?, ?)",
            (scope, content, source_message_id, now, now),
        )

    async def get(self, memory_id: int, scope: str | None = None) -> MemoryRecord | None:
        if scope is None:
            row = await self._db.fetchone(
                f"SELECT {_COLUMNS} FROM memories WHERE id=?", (memory_id,)
            )
        else:
            row = await self._db.fetchone(
                f"SELECT {_COLUMNS} FROM memories WHERE id=? AND scope=?",
                (memory_id, scope),
            )
        return _row_to_record(row) if row else None

    async def search(
        self, scope: str, query: str, limit: int = 5, offset: int = 0
    ) -> tuple[list[MemoryRecord], int]:
        pattern = f"%{_like_escape(query)}%"
        rows = await self._db.fetchall(
            f"SELECT {_COLUMNS} FROM memories WHERE scope=? AND status='active' "
            "AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ? OFFSET ?",
            (scope, pattern, limit, offset),
        )
        total_row = await self._db.fetchone(
            "SELECT COUNT(*) FROM memories WHERE scope=? AND status='active' "
            "AND content LIKE ? ESCAPE '\\'",
            (scope, pattern),
        )
        return [_row_to_record(r) for r in rows], int(total_row[0] if total_row else 0)

    async def list_page(
        self, scope: str, page: int = 1, page_size: int = PAGE_DEFAULT_SIZE
    ) -> tuple[list[MemoryRecord], int]:
        page = max(1, page)
        rows = await self._db.fetchall(
            f"SELECT {_COLUMNS} FROM memories WHERE scope=? AND status='active' "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (scope, page_size, (page - 1) * page_size),
        )
        total_row = await self._db.fetchone(
            "SELECT COUNT(*) FROM memories WHERE scope=? AND status='active'", (scope,)
        )
        return [_row_to_record(r) for r in rows], int(total_row[0] if total_row else 0)

    async def active_recent(self, scope: str, limit: int) -> list[MemoryRecord]:
        rows = await self._db.fetchall(
            f"SELECT {_COLUMNS} FROM memories WHERE scope=? AND status='active' "
            "ORDER BY id DESC LIMIT ?",
            (scope, limit),
        )
        return [_row_to_record(r) for r in rows]

    async def revise(self, memory_id: int, scope: str, new_content: str) -> int | None:
        """修订 = 旧行标记 superseded、插入 revision_of 指向旧行的新行。"""
        now = time.time()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT id FROM memories WHERE id=? AND scope=? AND status='active'",
                (memory_id, scope),
            )
            if row is None:
                return None
            await tx.execute(
                "UPDATE memories SET status='superseded', updated_ts=? WHERE id=?", (now, memory_id)
            )
            new_id = await tx.execute(
                "INSERT INTO memories (scope, content, status, source_message_id, revision_of, "
                "created_ts, updated_ts) VALUES (?, ?, 'active', NULL, ?, ?, ?)",
                (scope, new_content, memory_id, now, now),
            )
            return new_id

    async def soft_delete(self, memory_id: int, scope: str) -> bool:
        now = time.time()
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                "SELECT id FROM memories WHERE id=? AND scope=? AND status='active'",
                (memory_id, scope),
            )
            if row is None:
                return False
            await tx.execute(
                "UPDATE memories SET status='deleted', updated_ts=? WHERE id=? AND scope=?",
                (now, memory_id, scope),
            )
            return True

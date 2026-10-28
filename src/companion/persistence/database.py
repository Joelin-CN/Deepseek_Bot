"""SQLite 持久化通用层。

使用标准库 sqlite3（WAL 模式），单连接经 asyncio.Lock 串行化、实际
调用放线程执行器，避免阻塞事件循环；同一进程内并发协程安全。跨进
程访问不在当前范围（单实例运行见 M9 单实例保护）。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from companion.persistence.migrations import MIGRATIONS
from companion.persistence.migrations.base import Migration

logger = logging.getLogger(__name__)


class _Tx:
    """事务内的执行句柄；不重复获取连接锁（锁由 transaction 持有）。"""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await asyncio.to_thread(self._conn.execute, sql, params)
        return cursor.lastrowid or 0

    async def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        cursor = await asyncio.to_thread(self._conn.execute, sql, params)
        return await asyncio.to_thread(cursor.fetchone)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        cursor = await asyncio.to_thread(self._conn.execute, sql, params)
        return await asyncio.to_thread(cursor.fetchall)


class Database:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path | str) -> "Database":
        db = cls(path)
        await db._connect()
        await db._migrate()
        return db

    async def _connect(self) -> None:
        def _open() -> sqlite3.Connection:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

        self._conn = await asyncio.to_thread(_open)
        logger.info("数据库已打开: %s (sqlite %s)", self._path, sqlite3.sqlite_version)

    async def _migrate(self) -> None:
        assert self._conn is not None

        def _apply() -> list[Migration]:
            conn = self._conn
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_ts REAL NOT NULL)"
            )
            conn.commit()
            applied = {row[0] for row in conn.execute("SELECT version FROM _schema_migrations")}
            newly: list[Migration] = []
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                with conn:  # 每个迁移一个事务
                    for statement in migration.statements:
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO _schema_migrations (version, name, applied_ts) VALUES (?, ?, ?)",
                        (migration.version, migration.name, time.time()),
                    )
                newly.append(migration)
            return newly

        async with self._lock:
            applied_now = await asyncio.to_thread(_apply)
        for migration in applied_now:
            logger.info("已应用数据库迁移 v%d %s", migration.version, migration.name)

    @property
    def path(self) -> Path:
        return self._path

    async def version(self) -> int:
        row = await self.fetchone("SELECT COALESCE(MAX(version), 0) FROM _schema_migrations")
        return int(row[0]) if row else 0

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """执行单条语句（自动提交），返回 lastrowid。"""
        if self._conn is None:
            raise RuntimeError("数据库未打开")
        async with self._lock:
            def _run() -> int:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return cursor.lastrowid or 0

            return await asyncio.to_thread(_run)

    async def fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        if self._conn is None:
            raise RuntimeError("数据库未打开")
        async with self._lock:
            cursor = await asyncio.to_thread(self._conn.execute, sql, params)
            return await asyncio.to_thread(cursor.fetchone)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        if self._conn is None:
            raise RuntimeError("数据库未打开")
        async with self._lock:
            cursor = await asyncio.to_thread(self._conn.execute, sql, params)
            return await asyncio.to_thread(cursor.fetchall)

    @asynccontextmanager
    async def transaction(self):
        """多语句事务：异常回滚，正常提交；事务期间独占连接锁。"""
        if self._conn is None:
            raise RuntimeError("数据库未打开")
        async with self._lock:
            await asyncio.to_thread(self._conn.execute, "BEGIN IMMEDIATE")
            try:
                yield _Tx(self._conn)
            except BaseException:
                await asyncio.to_thread(self._conn.rollback)
                raise
            else:
                await asyncio.to_thread(self._conn.commit)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None
                logger.info("数据库已关闭: %s", self._path)

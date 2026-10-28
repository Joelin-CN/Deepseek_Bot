"""持久化层：迁移、重启恢复、并发写与事务。"""

import asyncio

import pytest

from companion.persistence import Database


async def test_migrations_apply_and_record_version(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    try:
        assert await db.version() == 2  # V1 messages/usage + V2 memories
        await db.execute(
            "INSERT INTO memories (scope, content, status, created_ts, updated_ts) "
            "VALUES ('s', 'c', 'active', 0, 0)"
        )
    finally:
        await db.close()


async def test_reopen_does_not_reapply_migrations(tmp_path):
    db1 = await Database.open(tmp_path / "bot.db")
    await db1.execute(
        "INSERT INTO memories (scope, content, status, created_ts, updated_ts) "
        "VALUES ('s', '重启后仍在我还在', 'active', 0, 0)"
    )
    await db1.close()

    db2 = await Database.open(tmp_path / "bot.db")
    try:
        assert await db2.version() == 2
        row = await db2.fetchone("SELECT content FROM memories WHERE scope='s'")
        assert row[0] == "重启后仍在我还在"
    finally:
        await db2.close()


async def test_concurrent_writes_are_serialized(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    try:
        async def insert(index: int) -> None:
            await db.execute(
                "INSERT INTO usage (ts, session_key, actor_id, source, model, "
                "prompt_tokens, completion_tokens, total_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (0.0, f"session-{index}", index, "chat", "deepseek-flash", 1, 2, 3),
            )

        await asyncio.gather(*(insert(i) for i in range(50)))
        row = await db.fetchone("SELECT COUNT(*) FROM usage")
        assert row[0] == 50
    finally:
        await db.close()


async def test_transaction_rolls_back_on_error(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    try:
        with pytest.raises(RuntimeError):
            async with db.transaction() as tx:
                await tx.execute(
                    "INSERT INTO usage (ts, session_key, actor_id, source, model, "
                    "prompt_tokens, completion_tokens, total_tokens) VALUES (0, 's', 1, 'c', 'm', 1, 1, 2)"
                )
                raise RuntimeError("boom")
        row = await db.fetchone("SELECT COUNT(*) FROM usage")
        assert row[0] == 0
    finally:
        await db.close()


async def test_transaction_commits_atomically(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    try:
        async with db.transaction() as tx:
            await tx.execute(
                "INSERT INTO usage (ts, session_key, actor_id, source, model, "
                "prompt_tokens, completion_tokens, total_tokens) VALUES (0, 'a', 1, 'c', 'm', 1, 1, 2)"
            )
            await tx.execute(
                "INSERT INTO usage (ts, session_key, actor_id, source, model, "
                "prompt_tokens, completion_tokens, total_tokens) VALUES (0, 'b', 1, 'c', 'm', 1, 1, 2)"
            )
        row = await db.fetchone("SELECT COUNT(*) FROM usage")
        assert row[0] == 2
    finally:
        await db.close()


async def test_wal_files_created_beside_database(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    try:
        await db.execute(
            "INSERT INTO memories (scope, content, status, created_ts, updated_ts) "
            "VALUES ('s', 'c', 'active', 0, 0)"
        )
        assert (tmp_path / "bot.db-wal").exists() or (tmp_path / "bot.db-shm").exists()
    finally:
        await db.close()

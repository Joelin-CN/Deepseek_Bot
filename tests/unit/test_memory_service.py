"""记忆服务：范围与权限强制（跨范围在结构上不可表达）。"""

import pytest

from companion.core.events import SessionKey
from companion.core.permissions import Capability, PermissionDenied, Role, RunContext, actor_for
from companion.memory import MemoryService
from companion.memory.store import MemoryStore
from companion.persistence import Database

SELF_ID = 3666167977
OWNER = 844281600
OTHER_MEMBER = 111111
STRANGER = 999999999

OWNER_SCOPE = f"qq/{SELF_ID}/private/{OWNER}"
OTHER_SCOPE = f"qq/{SELF_ID}/private/{OTHER_MEMBER}"
GROUP_SCOPE = f"qq/{SELF_ID}/group/4321"


def _ctx(user_id: int, *, chat_type: str = "private", target: int | None = None) -> RunContext:
    target = target if target is not None else user_id
    session = SessionKey("qq", SELF_ID, chat_type, target)
    actor = actor_for("qq", user_id, "测试", owner_id=OWNER, allowed_senders={OWNER, OTHER_MEMBER})
    return RunContext(session=session, actor=actor)


async def _service(tmp_path) -> tuple[MemoryService, Database]:
    db = await Database.open(tmp_path / "bot.db")
    return MemoryService.from_database(db), db


async def test_remember_and_retrieve_within_own_session(tmp_path):
    service, db = await _service(tmp_path)
    try:
        ctx = _ctx(OWNER)
        await service.remember(ctx, "我喜欢蓝色")
        items = await service.context_slice(ctx.session, max_items=5)
        assert items == ["我喜欢蓝色"]
    finally:
        await db.close()


async def test_remembered_content_survives_restart(tmp_path):
    service, db = await _service(tmp_path)
    await service.remember(_ctx(OWNER), "服务器续费在 12 月")
    await db.close()

    service2, db2 = await _service(tmp_path)
    try:
        items = await service2.context_slice(SessionKey("qq", SELF_ID, "private", OWNER), 5)
        assert items == ["服务器续费在 12 月"]
    finally:
        await db2.close()


async def test_private_sessions_do_not_leak_each_other(tmp_path):
    service, db = await _service(tmp_path)
    try:
        await service.remember(_ctx(OWNER), "主人的私聊记忆")
        await service.remember(_ctx(OTHER_MEMBER), "成员的私聊记忆")
        owner_items = await service.context_slice(SessionKey("qq", SELF_ID, "private", OWNER), 5)
        member_items = await service.context_slice(SessionKey("qq", SELF_ID, "private", OTHER_MEMBER), 5)
        assert owner_items == ["主人的私聊记忆"]
        assert member_items == ["成员的私聊记忆"]
    finally:
        await db.close()


async def test_group_scope_isolated_from_private(tmp_path):
    service, db = await _service(tmp_path)
    try:
        await service.remember(_ctx(OWNER), "私聊内容")
        group_session = SessionKey("qq", SELF_ID, "group", 4321)
        items = await service.context_slice(group_session, 5)
        assert items == []
    finally:
        await db.close()


async def test_forget_removes_from_context_immediately(tmp_path):
    service, db = await _service(tmp_path)
    try:
        ctx = _ctx(OWNER)
        mid = await service.remember(ctx, "要忘记的事")
        assert await service.forget(ctx, mid) is True
        assert await service.context_slice(ctx.session, 5) == []
        assert await service.forget(ctx, mid) is False
    finally:
        await db.close()


async def test_revise_updates_context_content(tmp_path):
    service, db = await _service(tmp_path)
    try:
        ctx = _ctx(OWNER)
        mid = await service.remember(ctx, "旧值")
        record = await service.revise(ctx, mid, "新值")
        assert record is not None
        items = await service.context_slice(ctx.session, 5)
        assert items == ["新值"]
    finally:
        await db.close()


async def test_stranger_cannot_use_memory(tmp_path):
    service, db = await _service(tmp_path)
    try:
        ctx = _ctx(STRANGER)
        assert ctx.actor.role is Role.UNKNOWN
        with pytest.raises(PermissionDenied):
            await service.remember(ctx, "x")
        with pytest.raises(PermissionDenied):
            await service.list_memories(ctx)
        with pytest.raises(PermissionDenied):
            await service.forget(ctx, 1)
    finally:
        await db.close()


async def test_context_slice_bounded_by_max_items(tmp_path):
    service, db = await _service(tmp_path)
    try:
        ctx = _ctx(OWNER)
        for i in range(5):
            await service.remember(ctx, f"记忆{i}")
        items = await service.context_slice(ctx.session, 2)
        assert len(items) == 2
        assert items == ["记忆4", "记忆3"]  # 最近的在前
    finally:
        await db.close()


async def test_store_not_exposed_cross_scope(tmp_path):
    service, db = await _service(tmp_path)
    try:
        await service.remember(_ctx(OWNER), "主人的记忆")
        # 成员会话按自己的 scope 查询，看不到主人的记忆
        other_ctx = _ctx(OTHER_MEMBER)
        records, total = await service.search(other_ctx, "主人的记忆")
        assert total == 0
        # 按 id 删除别人的记忆：scope 不匹配 → 找不到
        assert await service.forget(other_ctx, 1) is False
    finally:
        await db.close()

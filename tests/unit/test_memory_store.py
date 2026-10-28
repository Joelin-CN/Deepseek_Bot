"""长期记忆存储：CRUD、修订链、软删除、分页与中文检索。"""

from companion.memory.store import MemoryStore
from companion.persistence import Database

SCOPE_A = "qq/3666167977/private/844281600"
SCOPE_B = "qq/3666167977/private/111111"
GROUP_SCOPE = "qq/3666167977/group/4321"


async def test_add_get_and_active_recent(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        mid = await store.add(SCOPE_A, "我喜欢蓝色", source_message_id=101)
        await store.add(SCOPE_A, "项目部署在 E 盘")
        record = await store.get(mid, SCOPE_A)
        assert record is not None
        assert record.content == "我喜欢蓝色"
        assert record.status == "active"
        assert record.source_message_id == 101
        recent = await store.active_recent(SCOPE_A, 10)
        assert [r.content for r in recent] == ["项目部署在 E 盘", "我喜欢蓝色"]  # 新的在前
    finally:
        await db.close()


async def test_chinese_substring_search(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        await store.add(SCOPE_A, "我喜欢蓝色")
        await store.add(SCOPE_A, "我的服务器在 E 盘")
        await store.add(SCOPE_A, "周五例会别安排太早")
        rows, total = await store.search(SCOPE_A, "蓝色")
        assert total == 1
        assert rows[0].content == "我喜欢蓝色"
        rows, total = await store.search(SCOPE_A, "服务器")
        assert total == 1 and rows[0].content == "我的服务器在 E 盘"
        rows, total = await store.search(SCOPE_A, "不存在的内容")
        assert total == 0 and rows == []
    finally:
        await db.close()


async def test_search_escapes_like_wildcards(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        await store.add(SCOPE_A, "普通记忆")
        await store.add(SCOPE_A, "进度是 100% 完成")
        # '%' 只匹配字面百分号，不是任意字符通配
        rows, total = await store.search(SCOPE_A, "100%")
        assert total == 1 and rows[0].content == "进度是 100% 完成"
        # '_' 同样只匹配字面下划线
        rows, total = await store.search(SCOPE_A, "普_通")
        assert total == 0
    finally:
        await db.close()


async def test_revise_creates_revision_chain(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        old_id = await store.add(SCOPE_A, "旧内容")
        new_id = await store.revise(old_id, SCOPE_A, "新内容")
        assert new_id is not None and new_id != old_id
        old = await store.get(old_id, SCOPE_A)
        new = await store.get(new_id, SCOPE_A)
        assert old.status == "superseded"
        assert new.status == "active"
        assert new.revision_of == old_id
        # 检索只出 active
        rows, total = await store.search(SCOPE_A, "内容")
        assert total == 1 and rows[0].content == "新内容"
        # 重复修订同一旧 id 不可再改
        assert await store.revise(old_id, SCOPE_A, "again") is None
    finally:
        await db.close()


async def test_soft_delete_hides_from_all_retrieval(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        mid = await store.add(SCOPE_A, "要被忘记的事")
        assert await store.soft_delete(mid, SCOPE_A) is True
        rows, total = await store.search(SCOPE_A, "忘记")
        assert total == 0
        assert await store.active_recent(SCOPE_A, 10) == []
        rows, total = await store.list_page(SCOPE_A)
        assert total == 0
        # 再删同一条返回 False
        assert await store.soft_delete(mid, SCOPE_A) is False
    finally:
        await db.close()


async def test_scopes_are_isolated_in_store(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        await store.add(SCOPE_A, "私聊 A 的秘密")
        await store.add(SCOPE_B, "私聊 B 的秘密")
        await store.add(GROUP_SCOPE, "群的记录")
        rows_a, total_a = await store.search(SCOPE_A, "秘密")
        assert total_a == 1 and rows_a[0].scope == SCOPE_A
        rows_g, total_g = await store.search(GROUP_SCOPE, "记录")
        assert total_g == 1
        # scope 限定下的 get 找不到别的 scope 的记忆
        assert await store.get(1, SCOPE_B) is None
    finally:
        await db.close()


async def test_list_page_pagination(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MemoryStore(db)
    try:
        for i in range(7):
            await store.add(SCOPE_A, f"记忆{i}")
        page1, total = await store.list_page(SCOPE_A, page=1, page_size=5)
        assert total == 7 and len(page1) == 5
        page2, _ = await store.list_page(SCOPE_A, page=2, page_size=5)
        assert len(page2) == 2
        assert page1[0].content == "记忆6"  # 新的在前
    finally:
        await db.close()


async def test_restart_recovery(tmp_path):
    db1 = await Database.open(tmp_path / "bot.db")
    store1 = MemoryStore(db1)
    await store1.add(SCOPE_A, "重启后仍要记得")
    await db1.close()

    db2 = await Database.open(tmp_path / "bot.db")
    store2 = MemoryStore(db2)
    try:
        rows, total = await store2.search(SCOPE_A, "记得")
        assert total == 1 and rows[0].content == "重启后仍要记得"
    finally:
        await db2.close()

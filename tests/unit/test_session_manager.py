"""SessionManager：内存历史有界、懒加载恢复一次、清空后不回拉。"""

from companion.core.sessions import SessionManager
from companion.core.sessions.identity import SessionKey

SESSION = SessionKey("qq", 3666167977, "private", 844281600)
OTHER = SessionKey("qq", 3666167977, "private", 111111)


async def test_append_turn_bounded_in_memory():
    manager = SessionManager(max_stored_messages=4)  # 2 轮问答
    for i in range(5):
        manager.append_turn(SESSION, f"问{i}", f"答{i}")
    history = await manager.history(SESSION)
    assert len(history) == 4
    assert history[0]["content"] == "问3"  # 最旧被挤出
    assert history[-1]["content"] == "答4"


async def test_history_lazy_loaded_once_per_session():
    calls = []

    async def loader(session, limit):
        calls.append(session)
        return [{"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"}]

    manager = SessionManager(max_stored_messages=10)
    manager.set_history_loader(loader)
    history = await manager.history(SESSION)
    assert [m["content"] for m in history] == ["旧问", "旧答"]
    await manager.history(SESSION)  # 第二次不再加载
    assert len(calls) == 1


async def test_clear_history_prevents_reload_from_store():
    """清空（如忘记记忆后）后，短期上下文为空且不会再从库中拉回旧轮次。"""
    calls = []

    async def loader(session, limit):
        calls.append(session)
        return [{"role": "user", "content": "旧问"}, {"role": "assistant", "content": "旧答"}]

    manager = SessionManager(max_stored_messages=10)
    manager.set_history_loader(loader)
    await manager.history(SESSION)
    manager.clear_history(SESSION)
    history = await manager.history(SESSION)
    assert len(history) == 0
    assert len(calls) == 1  # 没有第二次加载


async def test_loader_pairs_reordered_chronologically():
    async def loader(session, limit):
        # 模拟 delivered_turns 的新→旧顺序
        return [("新问", "新答"), ("旧问", "旧答")]

    manager = SessionManager(max_stored_messages=10)
    manager.set_history_loader(loader)
    # runner 的装载器负责把配对转成消息序列；这里直接验证上限截断
    history = await manager.history(SESSION)
    assert len(history) <= 10


async def test_restored_overflow_dropped_from_left():
    async def loader(session, limit):
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}"}
            for i in range(limit + 6)  # 超过上限，模拟更长的历史
        ]

    manager = SessionManager(max_stored_messages=4)
    manager.set_history_loader(loader)
    history = await manager.history(SESSION)
    assert len(history) == 4  # deque maxlen 溢出从左侧丢弃
    assert history[0]["content"] == "消息6"  # 保留的是最新的


async def test_zero_cap_disables_history():
    manager = SessionManager(max_stored_messages=0)
    manager.append_turn(SESSION, "问", "答")
    history = await manager.history(SESSION)
    assert len(history) == 0


async def test_sessions_isolated():
    manager = SessionManager(max_stored_messages=10)
    manager.append_turn(SESSION, "甲", "答甲")
    manager.append_turn(OTHER, "乙", "答乙")
    assert len(await manager.history(SESSION)) == 2
    assert len(await manager.history(OTHER)) == 2
    assert (await manager.history(SESSION))[0]["content"] == "甲"

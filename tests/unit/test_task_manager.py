"""任务管理器：状态流转、取消权限、清理钩子与退出清理。"""

from __future__ import annotations

import asyncio

from companion.core.events import InboundMessage
from companion.core.tasks import TaskManager, TaskState
from companion.core.permissions import RunContext, actor_for
from companion.core.sessions.identity import SessionKey

OWNER = 844281600
SELF = 3666167977


def make_ctx(task_id: str | None = None, user_id: int = OWNER) -> RunContext:
    ctx = RunContext(
        session=SessionKey("qq", SELF, "private", user_id),
        actor=actor_for("qq", user_id, "测试", owner_id=OWNER, allowed_senders={OWNER}),
    )
    return ctx


def make_msg(text: str = "查一下今天的新闻", message_id: int = 1) -> InboundMessage:
    session = SessionKey("qq", SELF, "private", OWNER)
    return InboundMessage(
        message_id=message_id, session=session, sender_id=OWNER,
        sender_nickname="测试", sub_type="friend", text=text, time=0,
    )


async def test_lifecycle_states_and_listing():
    manager = TaskManager()
    ctx = make_ctx()
    record = manager.create(ctx, make_msg())
    assert manager.has_running() and record.state is TaskState.RUNNING

    manager.finish(record.task_id, TaskState.COMPLETED, note="2 轮")
    assert not manager.has_running()
    assert manager.get(record.task_id).state is TaskState.COMPLETED
    listed = manager.list_tasks()
    assert len(listed) == 1 and "COMPLETED" in listed[0].one_line()


async def test_cancel_requires_same_actor():
    manager = TaskManager()
    ctx = make_ctx()
    manager.create(ctx, make_msg())

    other = make_ctx(user_id=10001)  # 非发起者（虽是主人之外）也无权
    ok, msg = manager.cancel(ctx.task_id, other)
    assert ok is False and "只能取消自己" in msg

    ok, msg = manager.cancel(ctx.task_id, ctx)
    assert ok is True and ctx.cancel.is_set()  # 取消传导到 RunContext 事件


async def test_cancel_by_id_prefix():
    manager = TaskManager()
    ctx = make_ctx()
    manager.create(ctx, make_msg())
    ok, msg = manager.cancel(ctx.task_id[:8], ctx)
    assert ok is True


async def test_cancel_unknown_task():
    manager = TaskManager()
    ok, msg = manager.cancel("nope", make_ctx())
    assert ok is False and "未找到" in msg


async def test_cleanup_hooks_run_on_finish():
    manager = TaskManager()
    ctx = make_ctx()
    record = manager.create(ctx, make_msg())
    done = []

    async def cleanup_a():
        done.append("a")

    async def cleanup_b():
        raise RuntimeError("清理失败不影响其他钩子")

    async def cleanup_c():
        await asyncio.sleep(0.01)
        done.append("c")

    manager.attach_cleanup(record.task_id, cleanup_a)
    manager.attach_cleanup(record.task_id, cleanup_b)
    manager.attach_cleanup(record.task_id, cleanup_c)
    manager.finish(record.task_id, TaskState.CANCELLED)

    for _ in range(50):
        if len(done) == 2:
            break
        await asyncio.sleep(0.02)
    assert sorted(done) == ["a", "c"]  # b 抛错只记日志


async def test_cancel_all_waits_and_finalizes():
    manager = TaskManager()
    ctx1, ctx2 = make_ctx(), make_ctx()
    manager.create(ctx1, make_msg("任务一", 1))
    manager.create(ctx2, make_msg("任务二", 2))

    async def hold():
        while not (ctx1.cancel.is_set() and ctx2.cancel.is_set()):
            await asyncio.sleep(0.01)

    holder = asyncio.create_task(hold())
    await manager.cancel_all(note="进程退出")
    await holder
    assert not manager.has_running()
    states = [r.state for r in manager.list_tasks()]
    assert states == [TaskState.CANCELLED, TaskState.CANCELLED]


async def test_usage_counters_accumulate():
    manager = TaskManager()
    ctx = make_ctx()
    record = manager.create(ctx, make_msg())
    record.add_round(100, 20, 120)
    record.add_round(50, 10, 60)
    manager.finish(record.task_id, TaskState.COMPLETED)
    final = manager.get(record.task_id)
    assert final.model_rounds == 2 and final.total_tokens == 180 and final.prompt_tokens == 150


async def test_runner_task_control_strips_angle_brackets():
    """按提示复制时常见的 <ID> 尖括号应被容错剥离。"""
    from pydantic import SecretStr

    from companion.config.settings import BotSettings, DeepSeekSettings, OneBotSettings, Settings
    from companion.core.lifecycle.runner import BotRunner

    settings = Settings(
        onebot=OneBotSettings(access_token=SecretStr("t"), self_id=3666167977, ws_url="ws://127.0.0.1:1/"),
        bot=BotSettings(allowed_private_senders=[OWNER]),
        deepseek=DeepSeekSettings(),
    )
    runner = BotRunner(settings)
    ctx = make_ctx()
    manager = runner._task_manager
    manager.create(ctx, make_msg())

    reply = runner._handle_task_control(ctx, f"/task cancel <{ctx.task_id}>")
    assert "取消请求已发出" in reply and ctx.cancel.is_set()

    ctx2 = make_ctx()
    manager.create(ctx2, make_msg("另一个任务", 2))
    reply = runner._handle_task_control(ctx2, f"/task status <{ctx2.task_id[:8]}>")
    assert "RUNNING" in reply or "running" in reply.lower() or ctx2.task_id[:8] in reply

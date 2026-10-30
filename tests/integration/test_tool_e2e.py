"""M5 集成：真实 Runner 管线下的工具闭环与任务控制路径。

替身模型客户端（ScriptedClient）+ 真实插件宿主（builtin.hello 的
hello_greet 工具）验证：插件注册的工具经统一执行服务进入模型请求、
执行后按 tool_call_id 回传、最终答复送达。另验证 /task 控制命令在会话
锁被长任务持有时仍能立即处理并完成取消传导。
"""

from __future__ import annotations

import asyncio

from companion.adapters.deepseek import ChatResult, ToolCallData
from companion.config.settings import PluginInstanceSettings
from companion.core.agent.responder import DeepSeekResponder
from companion.core.sessions.store import UsageStore

from test_onebot_stub import (
    ALLOWED,
    StubOneBot,
    make_db,
    make_settings,
    private_event,
    running_bot,
    wait_for_status,
    wait_workers,
)


def text_event(message_id: int, text: str, user_id: int = ALLOWED) -> dict:
    return private_event(
        message_id=message_id, user_id=user_id,
        message=[{"type": "text", "data": {"text": text}}],
    )


def _reply_text(action: dict) -> str:
    return action["params"]["message"][0]["data"]["text"]


class ScriptedClient:
    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.requests: list[tuple[list, list | None]] = []

    async def chat(self, messages, *, tools=None):
        self.requests.append(([dict(m) for m in messages], tools))
        outcome = self.rounds.pop(0)
        return outcome

    async def close(self):
        pass


def _final(content: str) -> ChatResult:
    return ChatResult(content=content, model="deepseek-flash", prompt_tokens=20, completion_tokens=10, total_tokens=30)


def _with_call(call_id: str, name: str, arguments: str) -> ChatResult:
    return ChatResult(
        content="", model="deepseek-flash", prompt_tokens=20, completion_tokens=10, total_tokens=30,
        finish_reason="tool_calls", tool_calls=(ToolCallData(id=call_id, name=name, arguments=arguments),),
    )


class SlowCancelAwareResponder:
    """模拟长工具任务：注册任务记录、持有会话锁，直到取消事件才结束。"""

    def __init__(self):
        self.started = asyncio.Event()
        self.ctx = None
        self.task_manager = None
        self.record = None

    async def respond(self, message, ctx):
        self.ctx = ctx
        if self.task_manager is not None and self.record is None:
            self.record = self.task_manager.create(ctx, message)
        self.started.set()
        while not ctx.cancelled:
            await asyncio.sleep(0.05)
        if self.record is not None:
            from companion.core.tasks import TaskState
            self.task_manager.finish(self.record.task_id, TaskState.CANCELLED, note="控制命令取消")
        return "长任务已被取消。"

    async def close(self):
        pass


async def test_plugin_tool_loop_end_to_end(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    settings = make_settings(stub.port)
    settings.data_dir_resolved = tmp_path
    settings.plugins.instances["builtin.hello"] = PluginInstanceSettings(enabled=True)

    client = ScriptedClient(
        _with_call("call-1", "hello_greet", '{"name": "世界"}'),
        _final("问候完成：你好，世界！"),
    )

    class Placeholder:
        async def respond(self, message, ctx):
            return "占位"

        async def close(self):
            pass

    try:
        async with running_bot(settings, db, responder=Placeholder()) as (runner, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner)
            assert runner._tool_service is not None

            usage = UsageStore(db)
            runner._responder = DeepSeekResponder(
                client, settings.deepseek, sessions=runner._sessions, usage=usage,
                tools=runner._tool_service, task_manager=runner._task_manager,
            )

            await stub.push_event(text_event(2001, "用工具问候一下世界"))
            reply = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert reply == "问候完成：你好，世界！"
            await wait_for_status(db, 2001, "sent_ok")

            # 插件工具确实进入请求并被执行回传
            first_messages, first_tools = client.requests[0]
            assert first_tools and first_tools[0]["function"]["name"] == "hello_greet"
            second_messages, _ = client.requests[1]
            tool_msgs = [m for m in second_messages if m["role"] == "tool"]
            assert tool_msgs[0]["tool_call_id"] == "call-1"
            assert "世界" in tool_msgs[0]["content"]

            rows = await db.fetchall("SELECT source, total_tokens FROM usage ORDER BY id")
            assert len(rows) == 2 and all(r[0] == "chat_tools" for r in rows)  # 每轮用量落库
    finally:
        await db.close()
        await stub.stop()


async def test_task_control_bypasses_session_lock_and_cancels(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    slow = SlowCancelAwareResponder()
    settings = make_settings(stub.port)

    async def run_flow():
        async with running_bot(settings, db, responder=slow) as (runner, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner)
            slow.task_manager = runner._task_manager

            # 1) 长任务占用会话锁
            await stub.push_event(text_event(2101, "开始一个很慢的网页任务"))
            await asyncio.wait_for(slow.started.wait(), timeout=5)
            await asyncio.sleep(0.2)  # 确认 worker 正在等待取消（锁被持有）

            # 2) 控制命令不被会话锁阻塞：立即收到 list 输出
            await stub.push_event(text_event(2102, "/task list"))
            list_reply = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert "RUNNING" in list_reply and "网页任务" in list_reply

            # 3) 从 list 输出解析任务短 ID 并取消
            running_line = next(l for l in list_reply.splitlines() if "[RUNNING]" in l)
            task_id = running_line.split("[")[0].strip().lstrip("- ").strip()
            await stub.push_event(text_event(2103, f"/task cancel {task_id}"))
            cancel_reply = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=2))
            assert "取消请求已发出" in cancel_reply

            # 4) 长任务观察到取消事件退出，发送取消答复（第二个业务回复）
            final_reply = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=3))
            assert final_reply == "长任务已被取消。"
            for mid in (2101, 2102, 2103):
                await wait_for_status(db, mid, "sent_ok")

    try:
        await asyncio.wait_for(run_flow(), timeout=30)
        tasks_after = None
        assert slow.ctx is not None and slow.ctx.cancelled
    finally:
        await db.close()
        await stub.stop()


async def test_non_owner_cannot_use_task_control(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    member = 10002
    settings = make_settings(stub.port, allowed_private_senders=[ALLOWED, member])

    class Instant:
        async def respond(self, message, ctx):
            return "回复"

        async def close(self):
            pass

    try:
        async with running_bot(settings, db, responder=Instant()) as (runner, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner)
            await stub.push_event(text_event(2201, "/task list", user_id=member))
            reply = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert "仅限主人" in reply
            await wait_for_status(db, 2201, "sent_ok")
    finally:
        await db.close()
        await stub.stop()

"""工具调用循环：多轮闭环、限制、取消、用量、预算与待提交隔离。"""

from __future__ import annotations

import asyncio
import json

from companion.adapters.deepseek import ChatResult, DeepSeekError, ToolCallData
from companion.config.settings import DeepSeekSettings, ToolSettings
from companion.core.agent.responder import DeepSeekResponder
from companion.core.events import InboundMessage, SessionKey
from companion.core.tasks import TaskManager, TaskState
from companion.core.permissions import RunContext, actor_for
from companion.core.sessions import SessionManager
from companion.core.tools import ToolDescriptor, ToolExecutionService, ToolRegistry

OWNER = 844281600
SELF = 3666167977


def msg(text: str, message_id: int = 1, user_id: int = OWNER) -> InboundMessage:
    return InboundMessage(
        message_id=message_id, session=SessionKey("qq", SELF, "private", user_id),
        sender_id=user_id, sender_nickname="测试", sub_type="friend", text=text, time=0,
    )


def ctx_for(message: InboundMessage) -> RunContext:
    actor = actor_for("qq", message.sender_id, "测试", owner_id=OWNER, allowed_senders={OWNER})
    return RunContext(session=message.session, actor=actor)


def final(content: str) -> ChatResult:
    return ChatResult(content=content, model="deepseek-flash", prompt_tokens=20, completion_tokens=10, total_tokens=30)


def with_calls(*calls: ToolCallData, content: str = "") -> ChatResult:
    return ChatResult(
        content=content, model="deepseek-flash", prompt_tokens=20, completion_tokens=10,
        total_tokens=30, finish_reason="tool_calls", tool_calls=calls,
    )


class ScriptedClient:
    """按脚本逐轮返回；记录每次请求的 messages 与 tools。"""

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.requests: list[tuple[list[dict], list | None]] = []

    async def chat(self, messages, *, tools=None):
        self.requests.append(([dict(m) for m in messages], tools))
        if not self.rounds:
            raise AssertionError("脚本轮次已耗尽，模型又被请求")
        outcome = self.rounds.pop(0)
        if isinstance(outcome, DeepSeekError):
            raise outcome
        return outcome

    async def close(self):
        pass


class Usage:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


class Noticer:
    def __init__(self):
        self.sent = []

    async def __call__(self, message, text):
        self.sent.append(text)


def make_responder(client, *, registry=None, tool_settings=None, usage=None, noticer=None, task_manager=None, sessions=None):
    registry = registry if registry is not None else add_registry()
    service = ToolExecutionService(
        registry=registry, is_owner_active=lambda owner: True, tool_timeout=2.0, max_result_chars=4000
    )
    return DeepSeekResponder(
        client, DeepSeekSettings(),
        sessions=sessions or SessionManager(max_stored_messages=40),
        usage=usage, tools=service,
        tool_settings=tool_settings or ToolSettings(),
        task_manager=task_manager or TaskManager(),
        notice_sender=noticer,
    )


def add_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def add(params):
        return {"sum": params["a"] + params["b"]}

    registry.register(
        ToolDescriptor(
            name="add", description="加法",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
            owner="t.math",
        ),
        add,
    )
    return registry


async def test_plain_chat_without_tools_single_round():
    client = ScriptedClient(final("直接回答"))
    responder = make_responder(client, registry=ToolRegistry())
    message = msg("你好")
    reply = await responder.respond(message, ctx_for(message))
    assert reply == "直接回答"
    messages, tools = client.requests[0]
    assert tools is None and [m["role"] for m in messages] == ["system", "user"]


async def test_single_tool_roundtrip():
    client = ScriptedClient(
        with_calls(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":2}')),
        final("1+2=3"),
    )
    usage = Usage()
    noticer = Noticer()
    responder = make_responder(client, usage=usage, noticer=noticer)
    message = msg("1+2 等于几")
    reply = await responder.respond(message, ctx_for(message))
    assert reply == "1+2=3"

    first_messages, first_tools = client.requests[0]
    assert first_tools and first_tools[0]["function"]["name"] == "add"
    second_messages, _ = client.requests[1]
    roles = [m["role"] for m in second_messages]
    assert roles == ["system", "user", "assistant", "tool"]
    assistant_msg = second_messages[2]
    assert assistant_msg["tool_calls"][0]["id"] == "c1"  # 保留模型原始调用消息
    tool_msg = second_messages[3]
    assert tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"]) == {"sum": 3}

    await asyncio.sleep(0.01)  # 用量落库为异步调度
    assert len(usage.calls) == 2  # 每轮都记用量
    assert all(c["source"] == "chat_tools" for c in usage.calls)
    assert len(noticer.sent) == 1 and "/task cancel" in noticer.sent[0]


async def test_multiple_calls_in_one_response_each_gets_result():
    client = ScriptedClient(
        with_calls(
            ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}'),
            ToolCallData(id="c2", name="add", arguments='{"a":2,"b":2}'),
        ),
        final("两数之和分别是 2 和 4"),
    )
    responder = make_responder(client)
    message = msg("算两题")
    await responder.respond(message, ctx_for(message))
    second_messages, _ = client.requests[1]
    tool_messages = [m for m in second_messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert [json.loads(m["content"])["sum"] for m in tool_messages] == [2, 4]


async def test_unknown_tool_and_invalid_arguments_returned_to_model():
    client = ScriptedClient(
        with_calls(
            ToolCallData(id="c1", name="nope", arguments="{}"),
            ToolCallData(id="c2", name="add", arguments="{broken"),
        ),
        final("工具都失败了"),
    )
    responder = make_responder(client)
    message = msg("测试")
    await responder.respond(message, ctx_for(message))
    tool_messages = [m for m in client.requests[1][0] if m["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"])["error"] == "unknown_tool"
    assert json.loads(tool_messages[1]["content"])["error"] == "invalid_arguments"


async def test_duplicate_call_id_executed_once_and_answered_once():
    calls = []

    async def counting_add(params):
        calls.append(params)
        return {"sum": params["a"] + params["b"]}

    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            name="add", description="加法",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
            owner="t.math",
        ),
        counting_add,
    )
    client = ScriptedClient(
        with_calls(
            ToolCallData(id="dup", name="add", arguments='{"a":1,"b":2}'),
            ToolCallData(id="dup", name="add", arguments='{"a":1,"b":2}'),
        ),
        final("结果 3"),
    )
    responder = make_responder(client, registry=registry)
    message = msg("测试")
    await responder.respond(message, ctx_for(message))
    assert len(calls) == 1  # 重复 ID 不重复执行
    tool_messages = [m for m in client.requests[1][0] if m["role"] == "tool"]
    assert len(tool_messages) == 1 and tool_messages[0]["tool_call_id"] == "dup"  # 只回传一次


async def test_model_round_limit_ends_with_honest_failure():
    loop_rounds = [with_calls(ToolCallData(id=f"c{i}", name="add", arguments='{"a":1,"b":1}')) for i in range(9)]
    client = ScriptedClient(*loop_rounds)
    manager = TaskManager()
    responder = make_responder(client, tool_settings=ToolSettings(max_model_rounds=2, accept_notice=False), task_manager=manager)
    message = msg("无限循环题")
    reply = await responder.respond(message, ctx_for(message))
    assert "未完成" in reply and "模型轮数达到上限" in reply
    assert len(client.requests) == 2
    records = manager.list_tasks()
    assert records[0].state is TaskState.FAILED and "轮数" in records[0].note


async def test_tool_call_limit_emits_error_result_not_hang():
    client = ScriptedClient(
        with_calls(
            ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}'),
            ToolCallData(id="c2", name="add", arguments='{"a":1,"b":1}'),
            ToolCallData(id="c3", name="add", arguments='{"a":1,"b":1}'),
        ),
        final("不应到达"),
    )
    responder = make_responder(client, tool_settings=ToolSettings(max_tool_calls=2, accept_notice=False))
    message = msg("连续调用")
    reply = await responder.respond(message, ctx_for(message))
    # 超限调用得到明确错误结果，模型据此收尾；不能静默丢弃或悬挂
    tool_messages = [m for m in client.requests[1][0] if m["role"] == "tool"]
    assert json.loads(tool_messages[2]["content"])["error"] == "tool_call_limit"
    assert reply == "不应到达"  # 模型对失败信息的最终答复原样返回


async def test_task_timeout_ends_with_honest_failure():
    from types import SimpleNamespace

    client = ScriptedClient(with_calls(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}')))
    responder = make_responder(client, tool_settings=ToolSettings(accept_notice=False))
    # 注入已过期的任务时限（配置层有下限，测试直接替换限制对象）
    responder._tool_settings = SimpleNamespace(
        enable=True,
        max_model_rounds=5, max_tool_calls=8, tool_timeout=2.0,
        task_timeout=-1.0, max_tool_result_chars=6000, accept_notice=False,
    )
    message = msg("慢任务")
    reply = await responder.respond(message, ctx_for(message))
    assert "未完成" in reply and "总时限" in reply
    assert client.requests == []


async def test_cancel_before_round_aborts_without_request():
    client = ScriptedClient()
    responder = make_responder(client, tool_settings=ToolSettings(accept_notice=False))
    message = msg("取消我")
    ctx = ctx_for(message)
    ctx.cancel.set()
    reply = await responder.respond(message, ctx)
    assert reply == "任务已取消。"
    assert client.requests == []


async def test_cancel_during_tool_execution_stops_loop():
    registry = add_registry()

    async def slow_add(params):
        await asyncio.sleep(0.3)
        return {"sum": params["a"] + params["b"]}

    registry._tools["add"] = (registry._tools["add"][0], slow_add)
    client = ScriptedClient(
        with_calls(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}')),
        final("迟到的成功答复"),
    )
    manager = TaskManager()
    responder = make_responder(client, registry=registry, task_manager=manager,
                               tool_settings=ToolSettings(accept_notice=False, tool_timeout=5))

    async def cancel_during_tool():
        await asyncio.sleep(0.1)
        for record in manager.list_tasks():
            record.cancel.set()

    cancel_task = asyncio.create_task(cancel_during_tool())
    message = msg("先算再取消")
    reply = await responder.respond(message, ctx_for(message))
    await cancel_task
    assert reply == "任务已取消。"
    assert len(client.requests) == 1  # 工具中止后不再发起下一轮模型请求
    assert manager.list_tasks()[0].state is TaskState.CANCELLED


async def test_notice_sent_once_with_task_id():
    client = ScriptedClient(
        with_calls(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}')),
        with_calls(ToolCallData(id="c2", name="add", arguments='{"a":2,"b":2}')),
        final("好了"),
    )
    noticer = Noticer()
    responder = make_responder(client, noticer=noticer, tool_settings=ToolSettings(accept_notice=True))
    message = msg("两轮工具")
    await responder.respond(message, ctx_for(message))
    assert len(noticer.sent) == 1  # 多轮只提示一次


async def test_empty_final_response_reported():
    client = ScriptedClient(final("   "))
    responder = make_responder(client, tool_settings=ToolSettings(accept_notice=False))
    message = msg("空响应")
    reply = await responder.respond(message, ctx_for(message))
    assert "空响应" in reply


async def test_pending_isolated_between_sessions_and_bound_to_message():
    sessions = SessionManager(max_stored_messages=40)

    async def two_session_flow():
        client = ScriptedClient(final("答甲"), final("答乙"), final("答乙2"))
        responder = make_responder(client, sessions=sessions, tool_settings=ToolSettings(accept_notice=False))
        msg_a1, msg_a2 = msg("甲问1", 1), msg("甲问2", 2)
        msg_b1 = msg("乙问1", 11, user_id=22222)
        reply_a1 = await responder.respond(msg_a1, ctx_for(msg_a1))
        reply_b1 = await responder.respond(msg_b1, ctx_for(msg_b1))
        # 先提交乙，再提交甲——互不覆盖（M4 之前是单值会串写）
        await responder.commit(msg_b1, reply_b1)
        await responder.commit(msg_a1, reply_a1)
        reply_a2 = await responder.respond(msg_a2, ctx_for(msg_a2))
        await responder.commit(msg_a2, reply_a2)
        return reply_a1, reply_b1, reply_a2

    reply_a1, reply_b1, reply_a2 = await two_session_flow()
    assert (reply_a1, reply_b1, reply_a2) == ("答甲", "答乙", "答乙2")
    history_a = list(await sessions.history(SessionKey("qq", SELF, "private", OWNER)))
    history_b = list(await sessions.history(SessionKey("qq", SELF, "private", 22222)))
    assert [m["content"] for m in history_a] == ["甲问1", "答甲", "甲问2", "答乙2"]
    assert [m["content"] for m in history_b] == ["乙问1", "答乙"]


async def test_stale_commit_does_not_write_history():
    sessions = SessionManager(max_stored_messages=40)
    client = ScriptedClient(final("答1"), final("答2"))
    responder = make_responder(client, sessions=sessions, tool_settings=ToolSettings(accept_notice=False))
    first = msg("问1", 1)
    await responder.respond(first, ctx_for(first))
    second = msg("问2", 2)
    await responder.respond(second, ctx_for(second))
    await responder.commit(first, "迟到的旧回执")  # 旧任务的送达回执
    await responder.commit(second, "答2")
    history = list(await sessions.history(SessionKey("qq", SELF, "private", OWNER)))
    assert [m["content"] for m in history] == ["问2", "答2"]


async def test_budget_trim_drops_oldest_tool_round_as_pair():
    big_blob = "数" * 3000

    async def big(params):
        return {"blob": big_blob}

    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            name="add", description="加法",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
            owner="t.math",
        ),
        big,
    )
    rounds = [with_calls(ToolCallData(id=f"c{i}", name="add", arguments='{"a":1,"b":1}')) for i in range(4)]
    rounds.append(final("汇总完成"))
    client = ScriptedClient(*rounds)
    settings = DeepSeekSettings(max_input_tokens=4000)  # 逼出裁剪

    service = ToolExecutionService(registry=registry, is_owner_active=lambda o: True, max_result_chars=2500)
    responder = DeepSeekResponder(
        client, settings, sessions=SessionManager(max_stored_messages=40),
        tools=service, tool_settings=ToolSettings(max_model_rounds=8, accept_notice=False),
        task_manager=TaskManager(),
    )
    message = msg("大结果任务")
    reply = await responder.respond(message, ctx_for(message))
    assert reply == "汇总完成"
    # 最后一轮请求：assistant(tool_calls) 与其 tool 消息必须成对出现
    last_messages, _ = client.requests[-1]
    tool_call_msgs = [i for i, m in enumerate(last_messages) if m.get("tool_calls")]
    for i in tool_call_msgs:
        nxt = last_messages[i + 1]
        assert nxt["role"] == "tool"  # 调用与结果保持配对
    # 曾经出现过丢弃日志（无法直接观察日志，这里断言请求规模受控）
    assert len(client.requests) == 5


async def test_tool_trace_not_committed_to_history():
    client = ScriptedClient(
        with_calls(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":2}')),
        final("最终答案"),
    )
    sessions = SessionManager(max_stored_messages=40)
    responder = make_responder(client, sessions=sessions, tool_settings=ToolSettings(accept_notice=False))
    message = msg("带工具的问题")
    reply = await responder.respond(message, ctx_for(message))
    await responder.commit(message, reply)
    history = list(await sessions.history(SessionKey("qq", SELF, "private", OWNER)))
    # 只有一轮 user/assistant；工具轨迹不进长期聊天历史
    assert [m["content"] for m in history] == ["带工具的问题", "最终答案"]


async def test_content_from_tool_round_not_dropped():
    """带 tool_calls 的轮次若携带实质正文，最终答复必须包含它（真实缺陷回归）。"""
    client = ScriptedClient(
        ChatResult(
            content="DeepSeek 当前在售模型共三款：deepseek-flash（通用）、deepseek-v4-pro（旗舰）与 deepseek-reasoner（推理），详见 https://api-docs.deepseek.com/quick_start/pricing 。",
            model="deepseek-flash", prompt_tokens=20, completion_tokens=200, total_tokens=220,
            finish_reason="tool_calls",
            tool_calls=(ToolCallData(id="c1", name="add", arguments='{"a":1,"b":1}'),),
        ),
        final("以上就是归纳，希望有帮助。"),
    )
    responder = make_responder(client, tool_settings=ToolSettings(accept_notice=False))
    message = msg("归纳一下")
    reply = await responder.respond(message, ctx_for(message))
    assert "deepseek-flash" in reply  # 工具轮次承载的正文没有丢
    assert "https://api-docs.deepseek.com/quick_start/pricing" in reply  # 来源链接保留
    assert reply.endswith("以上就是归纳，希望有帮助。")  # 最终轮内容也保留

"""DeepSeekResponder：会话隔离、有界上下文、总输入预算、记忆注入、
送达后才提交对话历史、用量与长度限额。"""

import asyncio

from pydantic import SecretStr

from companion.adapters.deepseek import ChatResult, DeepSeekError
from companion.config.settings import (
    BotSettings,
    DeepSeekSettings,
    OneBotSettings,
    Settings,
)
from companion.core.agent import DeepSeekResponder, build_responder
from companion.core.agent.context import estimate_tokens
from companion.core.events import InboundMessage, SessionKey
from companion.core.permissions import RunContext, actor_for
from companion.core.pipeline import FixedReplyResponder
from companion.core.sessions import SessionManager

SELF_ID = 3666167977
OWNER = 844281600
OTHER = 111111


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[list[dict]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def chat(self, messages):
        self.requests.append(messages)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.05)
        self.in_flight -= 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, DeepSeekError):
            raise outcome
        return outcome

    async def close(self):
        pass


def msg(text: str, message_id: int = 1, user_id: int = OWNER) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        session=SessionKey("qq", SELF_ID, "private", user_id),
        sender_id=user_id,
        sender_nickname="Zero",
        sub_type="friend",
        text=text,
        time=1793182844,
    )


def ctx_for(message: InboundMessage) -> RunContext:
    actor = actor_for(
        message.session.platform, message.sender_id, "测试用户",
        owner_id=OWNER, allowed_senders={OWNER, OTHER},
    )
    return RunContext(session=message.session, actor=actor)


def ask(text: str, message_id: int = 1, user_id: int = OWNER):
    message = msg(text, message_id, user_id)
    return message, ctx_for(message)


def result(content: str) -> ChatResult:
    return ChatResult(
        content=content, model="deepseek-flash",
        prompt_tokens=10, completion_tokens=5, total_tokens=15,
    )


async def test_first_reply_is_system_plus_user_only():
    client = FakeClient(result("你好"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    reply = await responder.respond(*ask("在吗"))
    assert reply == "你好"
    assert client.requests[0] == [
        {"role": "system", "content": "你是一个友好的 QQ 私聊机器人助手，用简洁的中文回答。"},
        {"role": "user", "content": "在吗"},
    ]


async def test_history_enters_only_after_commit():
    """生成结果不直接入史；确认送达（commit）后下一轮才带上。"""
    client = FakeClient(result("答1"), result("答2"), result("答3"))
    responder = DeepSeekResponder(client, DeepSeekSettings())

    await responder.respond(*ask("问1", 1))
    await responder.respond(*ask("问2", 2))  # 未 commit 问1 → 不带历史
    assert [m["role"] for m in client.requests[1]] == ["system", "user"]

    await responder.commit(msg("问2", 2), "答2")  # 模拟问2送达成功
    await responder.respond(*ask("问3", 3))
    assert [m["role"] for m in client.requests[2]] == ["system", "user", "assistant", "user"]
    assert client.requests[2][-2]["content"] == "答2"


async def test_commit_records_delivered_text_not_full_model_output():
    """发送前截断的回复，历史记录的是实际送达（截断后）的文本。"""
    client = FakeClient(result("长" * 5000), result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(max_reply_chars=100))
    delivered = await responder.respond(*ask("问", 1))
    assert "已截断" in delivered and len(delivered) < 200

    await responder.commit(msg("问", 1), delivered)
    await responder.respond(*ask("问2", 2))
    assistant_in_history = client.requests[1][-2]
    assert assistant_in_history["content"] == delivered


async def test_error_reply_is_not_committed():
    client = FakeClient(DeepSeekError("auth", "HTTP 401", 401), result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    error_reply = await responder.respond(*ask("在吗", 1))
    assert "DEEPSEEK_API_KEY" in error_reply

    await responder.commit(msg("在吗", 1), error_reply)  # 错误提示不构成对话轮次
    await responder.respond(*ask("再问", 2))
    assert [m["role"] for m in client.requests[1]] == ["system", "user"]


async def test_history_bounded_in_memory_by_session_manager():
    sessions = SessionManager(max_stored_messages=4)  # 两轮问答
    client = FakeClient(*(result(f"答{i}") for i in range(5)))
    responder = DeepSeekResponder(client, DeepSeekSettings(), sessions=sessions)
    for i in range(5):
        await responder.respond(*ask(f"问{i}", i))
        await responder.commit(msg(f"问{i}", i), f"答{i}")
    history = await sessions.history(msg("x").session)
    assert len(history) == 4  # 内存中的历史被上限约束，不只裁剪请求副本
    assert history[0]["content"] == "问3"  # 最旧的被挤出


async def test_sessions_have_isolated_histories():
    client = FakeClient(result("甲的回复"), result("乙的回复"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    await responder.respond(*ask("甲的问题", 1, user_id=OWNER))
    await responder.commit(msg("甲的问题", 1, user_id=OWNER), "甲的回复")
    await responder.respond(*ask("乙的问题", 2, user_id=OTHER))
    second_request = client.requests[1]
    assert [m["role"] for m in second_request] == ["system", "user"]
    assert second_request[-1]["content"] == "乙的问题"


class BigMemory:
    def __init__(self, items):
        self._items = items

    async def context_slice(self, session, max_items):
        return self._items[:max_items]


async def test_memory_injected_into_system_prompt():
    memory = BigMemory(["我喜欢蓝色", "服务器在 E 盘"])
    client = FakeClient(result("好的"))
    responder = DeepSeekResponder(
        client, DeepSeekSettings(memory_context_items=2), memory=memory
    )
    await responder.respond(*ask("你还记得什么"))
    system = client.requests[0][0]
    assert "我喜欢蓝色" in system["content"]
    assert "服务器在 E 盘" in system["content"]


async def test_memory_drops_oldest_under_total_budget():
    """总预算装不下全部记忆时，从最旧开始丢弃（列表按新→旧，pop 尾部最旧）。"""
    memory = BigMemory(["最新的记忆" + "节" * 40, "最旧的记忆" + "细" * 220])
    client = FakeClient(result("好"))
    settings = DeepSeekSettings(memory_context_items=2, max_input_tokens=256)
    responder = DeepSeekResponder(client, settings, memory=memory)
    await responder.respond(*ask("hi"))
    system = client.requests[0][0]["content"]
    assert "最新的记忆" in system
    assert "最旧的记忆" not in system


async def test_total_input_budget_covers_all_parts():
    """系统提示 + 记忆 + 历史 + 当前消息的总估算不超过预算。"""
    memory = BigMemory(["记忆条目" * 10] * 3)
    sessions = SessionManager(max_stored_messages=20)
    for i in range(6):
        sessions.append_turn(msg("x").session, f"很长的历史问题{i}" * 20, f"很长的历史回答{i}" * 20)
    client = FakeClient(result("答"))
    settings = DeepSeekSettings(max_input_tokens=600, memory_context_items=3)
    responder = DeepSeekResponder(client, settings, sessions=sessions, memory=memory)
    await responder.respond(*ask("当前也很长的消息" * 30, 9))
    request = client.requests[0]
    total = sum(estimate_tokens(m["content"]) for m in request)
    assert total <= settings.max_input_tokens + 16, f"总输入 {total} 超预算"


async def test_user_message_truncated_when_alone_over_budget():
    client = FakeClient(result("答"))
    settings = DeepSeekSettings(max_input_tokens=300)
    responder = DeepSeekResponder(client, settings)
    await responder.respond(*ask("超" * 1000, 1))
    user_message = client.requests[0][-1]
    assert "已截断" in user_message["content"]
    total = sum(estimate_tokens(m["content"]) for m in client.requests[0])
    assert total <= settings.max_input_tokens + 16


async def test_zero_history_disables_context():
    client = FakeClient(result("答"), result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(max_history_messages=0))
    await responder.respond(*ask("问1", 1))
    await responder.commit(msg("问1", 1), "答")
    await responder.respond(*ask("问2", 2))
    assert [m["role"] for m in client.requests[1]] == ["system", "user"]


async def test_history_trimmed_by_token_budget():
    client = FakeClient(*(result("答") for _ in range(3)))
    settings = DeepSeekSettings(max_history_messages=100, max_history_tokens=10)
    responder = DeepSeekResponder(client, settings)
    long_text = "很长的消息" * 20
    await responder.respond(*ask(long_text, 1))
    await responder.commit(msg(long_text, 1), "答")
    await responder.respond(*ask(long_text, 2))
    await responder.commit(msg(long_text, 2), "答")
    await responder.respond(*ask(long_text, 3))
    third_request = client.requests[2]
    assert [m["role"] for m in third_request] == ["system", "user"]


class RecordingUsage:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


async def test_usage_recorded_to_sink():
    usage = RecordingUsage()
    client = FakeClient(result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(), usage=usage)
    await responder.respond(*ask("问", 7))
    await asyncio.sleep(0)  # 用量落库为异步调度，让出一拍再断言
    assert len(usage.calls) == 1
    call = usage.calls[0]
    assert call["actor_id"] == OWNER
    assert call["source"] == "chat"
    assert call["total_tokens"] == 15
    assert call["session_key"] == f"qq/{SELF_ID}/private/{OWNER}"


async def test_model_concurrency_capped():
    outcomes = [result(f"答{i}") for i in range(10)]
    client = FakeClient(*outcomes)
    responder = DeepSeekResponder(client, DeepSeekSettings(model_concurrency=2))
    await asyncio.gather(*(responder.respond(*ask(f"问{i}", i)) for i in range(10)))
    assert client.max_in_flight <= 2


async def test_build_responder_fixed_mode(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = Settings(
        onebot=OneBotSettings(access_token=SecretStr("t"), self_id=SELF_ID),
        bot=BotSettings(allowed_private_senders=[OWNER], reply_mode="fixed"),
    )
    responder = build_responder(settings)
    assert isinstance(responder, FixedReplyResponder)
    assert await responder.respond(*ask("hi")) == "收到，QQ 链路已连通。"

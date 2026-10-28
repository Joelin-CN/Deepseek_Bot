"""DeepSeekResponder：会话隔离、有界上下文、记忆注入、用量与长度限额。"""

import asyncio
import logging

from pydantic import SecretStr

from companion.adapters.deepseek import ChatResult, DeepSeekError
from companion.config.settings import (
    BotSettings,
    DeepSeekSettings,
    OneBotSettings,
    Settings,
)
from companion.core.agent import DeepSeekResponder, build_responder
from companion.core.events import InboundMessage, SessionKey
from companion.core.pipeline import FixedReplyResponder

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


def result(content: str) -> ChatResult:
    return ChatResult(
        content=content, model="deepseek-flash",
        prompt_tokens=10, completion_tokens=5, total_tokens=15,
    )


async def test_first_reply_is_system_plus_user_only():
    client = FakeClient(result("你好"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    reply = await responder.respond(msg("在吗"))
    assert reply == "你好"
    assert client.requests[0] == [
        {"role": "system", "content": "你是一个友好的 QQ 私聊机器人助手，用简洁的中文回答。"},
        {"role": "user", "content": "在吗"},
    ]


async def test_history_accumulates_within_message_bound():
    client = FakeClient(result("答1"), result("答2"), result("答3"))
    settings = DeepSeekSettings(max_history_messages=2)
    responder = DeepSeekResponder(client, settings)
    await responder.respond(msg("问1", 1))
    await responder.respond(msg("问2", 2))
    await responder.respond(msg("问3", 3))
    third_request = client.requests[2]
    assert [m["role"] for m in third_request] == ["system", "user", "assistant", "user"]
    assert third_request[-1]["content"] == "问3"


async def test_history_trimmed_by_token_budget():
    client = FakeClient(*(result("答") for _ in range(3)))
    settings = DeepSeekSettings(max_history_messages=100, max_history_tokens=10)
    responder = DeepSeekResponder(client, settings)
    long_text = "很长的消息" * 20  # 每条远超 10 token
    await responder.respond(msg(long_text, 1))
    await responder.respond(msg(long_text, 2))
    await responder.respond(msg(long_text, 3))
    third_request = client.requests[2]
    # token 上限生效：历史被裁剪，只剩 system + 最新 user
    assert [m["role"] for m in third_request] == ["system", "user"]


async def test_zero_history_disables_context():
    client = FakeClient(result("答"), result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(max_history_messages=0))
    await responder.respond(msg("问1", 1))
    await responder.respond(msg("问2", 2))
    assert [m["role"] for m in client.requests[1]] == ["system", "user"]


async def test_sessions_have_isolated_histories():
    client = FakeClient(result("甲的回复"), result("乙的回复"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    await responder.respond(msg("甲的问题", 1, user_id=OWNER))
    await responder.respond(msg("乙的问题", 2, user_id=OTHER))
    second_request = client.requests[1]
    assert [m["role"] for m in second_request] == ["system", "user"]
    assert second_request[-1]["content"] == "乙的问题"  # 乙的请求里没有甲的对话


async def test_memory_injected_into_system_prompt():
    class Memory:
        def __init__(self):
            self.sessions_seen = []

        async def context_slice(self, session, max_items):
            self.sessions_seen.append(session)
            return ["我喜欢蓝色", "服务器在 E 盘"][:max_items]

    memory = Memory()
    client = FakeClient(result("好的"))
    responder = DeepSeekResponder(client, DeepSeekSettings(memory_context_items=2), memory=memory)
    await responder.respond(msg("你还记得什么"))
    system = client.requests[0][0]
    assert "我喜欢蓝色" in system["content"]
    assert "服务器在 E 盘" in system["content"]
    assert memory.sessions_seen[0].chat_target == OWNER


async def test_memory_disabled_by_config():
    class Memory:
        async def context_slice(self, session, max_items):
            raise AssertionError("不应被调用")

    client = FakeClient(result("好"))
    responder = DeepSeekResponder(client, DeepSeekSettings(memory_context_items=0), memory=Memory())
    await responder.respond(msg("hi"))
    system = client.requests[0][0]
    assert "长期记忆" not in system["content"]


async def test_error_returns_user_message():
    client = FakeClient(DeepSeekError("auth", "HTTP 401", 401))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    reply = await responder.respond(msg("在吗"))
    assert "DEEPSEEK_API_KEY" in reply


async def test_reply_truncated_to_configured_limit():
    client = FakeClient(result("长" * 5000))
    responder = DeepSeekResponder(client, DeepSeekSettings(max_reply_chars=100))
    reply = await responder.respond(msg("hi"))
    assert len(reply) < 200
    assert "已截断" in reply


class RecordingUsage:
    def __init__(self):
        self.calls = []

    async def record(self, **kwargs):
        self.calls.append(kwargs)


async def test_usage_recorded_to_sink():
    usage = RecordingUsage()
    client = FakeClient(result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(), usage=usage)
    await responder.respond(msg("问", 7))
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
    await asyncio.gather(*(responder.respond(msg(f"问{i}", i)) for i in range(10)))
    assert client.max_in_flight <= 2


async def test_build_responder_fixed_mode(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = Settings(
        onebot=OneBotSettings(access_token=SecretStr("t"), self_id=SELF_ID),
        bot=BotSettings(allowed_private_senders=[OWNER], reply_mode="fixed"),
    )
    responder = build_responder(settings)
    assert isinstance(responder, FixedReplyResponder)
    assert await responder.respond(msg("hi")) == "收到，QQ 链路已连通。"

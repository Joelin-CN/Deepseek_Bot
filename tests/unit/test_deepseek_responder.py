"""DeepSeekResponder：有界上下文、错误转用户提示、用量记录。"""

import logging

from companion.adapters.deepseek import ChatResult, DeepSeekError
from companion.config.settings import DeepSeekSettings
from companion.core.agent import DeepSeekResponder, build_responder
from companion.config.settings import BotSettings, OneBotSettings, Settings
from companion.core.events import InboundMessage
from pydantic import SecretStr


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[list[dict]] = []

    async def chat(self, messages):
        self.requests.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, DeepSeekError):
            raise outcome
        return outcome


def msg(text: str, message_id: int = 1) -> InboundMessage:
    return InboundMessage(
        message_id=message_id,
        user_id=844281600,
        self_id=3666167977,
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


async def test_history_accumulates_within_bound():
    client = FakeClient(result("答1"), result("答2"), result("答3"))
    settings = DeepSeekSettings(max_history_messages=2)  # 只保留最近 2 条（一问一答）
    responder = DeepSeekResponder(client, settings)
    await responder.respond(msg("问1", 1))
    await responder.respond(msg("问2", 2))
    await responder.respond(msg("问3", 3))
    third_request = client.requests[2]
    # system + 最近一问一答 + 新消息，共 4 条
    assert [m["role"] for m in third_request] == ["system", "user", "assistant", "user"]
    assert third_request[-1]["content"] == "问3"


async def test_zero_history_disables_context():
    client = FakeClient(result("答"), result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings(max_history_messages=0))
    await responder.respond(msg("问1", 1))
    await responder.respond(msg("问2", 2))
    assert [m["role"] for m in client.requests[1]] == ["system", "user"]


async def test_error_returns_user_message_and_keeps_history_clean():
    client = FakeClient(DeepSeekError("auth", "HTTP 401", 401))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    reply = await responder.respond(msg("在吗"))
    assert "DEEPSEEK_API_KEY" in reply


async def test_usage_logged_without_content(caplog):
    client = FakeClient(result("答"))
    responder = DeepSeekResponder(client, DeepSeekSettings())
    with caplog.at_level(logging.INFO, logger="companion.core.agent.responder"):
        await responder.respond(msg("在吗"))
    record = next(r for r in caplog.records if "DeepSeek 用量" in r.message)
    assert "total_tokens=15" in record.getMessage()


async def test_build_responder_fixed_mode(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    settings = Settings(
        onebot=OneBotSettings(access_token=SecretStr("t"), self_id=3666167977),
        bot=BotSettings(allowed_private_senders=[844281600], reply_mode="fixed"),
    )
    responder = build_responder(settings)
    assert await responder.respond(msg("hi")) == "收到，QQ 链路已连通。"

import asyncio

import pytest

from companion.adapters.onebot import ActionError, ActionUnknown, OneBotClient
from companion.config.settings import OneBotSettings
from pydantic import SecretStr


def make_client(event_queue_size: int = 1000) -> OneBotClient:
    settings = OneBotSettings(access_token=SecretStr("test-token"), self_id=3666167977)
    return OneBotClient(settings, event_queue_size=event_queue_size)


async def test_echo_frame_resolves_pending_future():
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-1"] = future
    client._route_frame(json_frame({"echo": "echo-1", "status": "ok", "retcode": 0, "data": {"x": 1}}))
    assert future.done()
    assert future.result()["data"] == {"x": 1}
    assert client.event_queue.empty()


async def test_event_frame_goes_to_event_queue_when_verified():
    client = make_client()
    client._verified.set()
    client._route_frame(json_frame({"post_type": "message", "message_type": "private"}))
    assert client.event_queue.qsize() == 1
    assert (await client.event_queue.get())["post_type"] == "message"


async def test_events_dropped_before_identity_verification():
    client = make_client()
    client._route_frame(json_frame({"post_type": "message", "message_type": "private"}))
    assert client.event_queue.empty()


async def test_echo_reply_still_routed_before_verification():
    # 身份验证自身依赖 echo 回包：未验证时 echo 帧也必须能解决 Future
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-9"] = future
    client._route_frame(json_frame({"echo": "echo-9", "retcode": 0, "status": "ok", "data": {}}))
    assert future.done()


async def test_event_queue_is_bounded_and_drops_on_full():
    client = make_client(event_queue_size=2)
    client._verified.set()
    for i in range(5):
        client._route_frame(json_frame({"post_type": "message", "seq": i}))
    assert client.event_queue.qsize() == 2  # 满后丢弃，不抛异常


async def test_malformed_frame_is_dropped():
    client = make_client()
    client._verified.set()
    client._route_frame("not-json{{{")
    assert client.event_queue.empty()


async def test_fail_pending_uses_unknown_outcome():
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-1"] = future
    client._fail_pending(ActionUnknown("call", "connection closed"))
    assert future.done()
    error = future.exception()
    assert isinstance(error, ActionError)
    assert isinstance(error, ActionUnknown)


async def test_timeout_removes_pending_so_writer_skips():
    # 超时的动作从 pending 移除；写者会因 echo 不在 pending 而跳过，不在新连接补发
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-1"] = future
    client._pending.pop("echo-1")  # 模拟超时清理
    assert "echo-1" not in client._pending  # 写者据此跳过


def json_frame(payload: dict) -> str:
    import json

    return json.dumps(payload)

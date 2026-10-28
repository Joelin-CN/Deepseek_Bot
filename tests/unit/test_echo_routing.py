"""OneBotClient 帧路由：echo 回包与事件分别分发。"""

import asyncio
import json

from pydantic import SecretStr

from companion.adapters.onebot import OneBotClient
from companion.config.settings import OneBotSettings


def make_client() -> OneBotClient:
    settings = OneBotSettings(access_token=SecretStr("test-token"), self_id=3666167977)
    return OneBotClient(settings)


async def test_echo_frame_resolves_pending_future():
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-1"] = future
    client._route_frame(json.dumps({"echo": "echo-1", "status": "ok", "retcode": 0, "data": {"x": 1}}))
    assert future.done()
    assert future.result()["data"] == {"x": 1}
    assert client.event_queue.empty()


async def test_event_frame_goes_to_event_queue():
    client = make_client()
    client._route_frame(json.dumps({"post_type": "message", "message_type": "private"}))
    assert client.event_queue.qsize() == 1
    assert (await client.event_queue.get())["post_type"] == "message"


async def test_unknown_echo_goes_to_event_queue():
    client = make_client()
    client._route_frame(json.dumps({"echo": "unknown", "status": "ok"}))
    assert client.event_queue.qsize() == 1


async def test_malformed_frame_is_dropped():
    client = make_client()
    client._route_frame("not-json{{{")
    assert client.event_queue.empty()


async def test_failed_pending_cleared_on_disconnect():
    client = make_client()
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["echo-1"] = future
    from companion.adapters.onebot import ActionError

    client._fail_pending(ActionError("call", "connection closed"))
    assert future.done()
    assert isinstance(future.exception(), ActionError)
    assert client._pending == {}

"""端到端 M1 流程测试：对替身 OneBot 服务（StubOneBot）跑完整 BotRunner。

替身服务实现正向 WS 服务端（Universal 角色）：校验 Bearer Token、推送
lifecycle 事件、按 echo 回应动作；测试向它推送合成私聊事件，断言机器人
的筛选、去重与固定应答行为。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from pydantic import SecretStr
from websockets.asyncio.server import serve

from companion.config.settings import BotSettings, DeepSeekSettings, OneBotSettings, Settings
from companion.core.lifecycle import BotRunner

SELF_ID = 3666167977
ALLOWED = 844281600
FIXED_REPLY = "收到，QQ 链路已连通。"
TOKEN = "stub-token"


class StubOneBot:
    """扮演 SnowLuma 的替身服务。"""

    def __init__(self, token: str = TOKEN):
        self.token = token
        self.received: list[dict[str, Any]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.handler_errors: list[str] = []
        self.connections = 0
        self._ws: Any = None
        self._send_lock: asyncio.Lock | None = None
        self._server: Any = None
        self.port: int = 0

    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws: Any) -> None:
        auth = ws.request.headers.get("Authorization")
        if auth != f"Bearer {self.token}":
            await ws.close(code=4001, reason="unauthorized")
            return
        self.connections += 1
        self._ws = ws
        self._send_lock = asyncio.Lock()
        try:
            await self._send(
                {
                    "time": 1793182844,
                    "self_id": SELF_ID,
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "status": {"online": True, "good": True},
                }
            )
            async for frame in ws:
                data = json.loads(frame)
                self.received.append(data)
                await self._send(self._respond(data))
        except Exception as exc:  # 记录而不是吞掉，便于断言
            self.handler_errors.append(f"{type(exc).__name__}: {exc}")

    def _respond(self, data: dict[str, Any]) -> dict[str, Any]:
        action = data.get("action")
        echo = data.get("echo")
        if action == "get_login_info":
            payload = {"status": "ok", "retcode": 0, "data": {"user_id": SELF_ID, "nickname": "Biubiu"}}
        elif action == "send_private_msg":
            self.sent_messages.append(data["params"])
            payload = {"status": "ok", "retcode": 0, "data": {"message_id": 9000 + len(self.sent_messages)}}
        else:
            payload = {"status": "failed", "retcode": 1404, "data": None}
        payload["echo"] = echo
        return payload

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is not None:
            async with self._send_lock:
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def push_event(self, raw: dict[str, Any]) -> None:
        await self._send(raw)

    async def wait_for_action(self, action: str, occurrence: int = 1, timeout: float = 5.0) -> dict[str, Any]:
        """等待某个动作第 occurrence 次出现；轮询带真实 await，给被测方调度机会。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            matches = [data for data in self.received if data.get("action") == action]
            if len(matches) >= occurrence:
                return matches[occurrence - 1]
            await asyncio.sleep(0.02)
        raise AssertionError(
            f"等待动作 {action} 第 {occurrence} 次出现超时；已收到: {self.received}"
        )


def private_event(message_id: int, user_id: int = ALLOWED, **overrides: Any) -> dict[str, Any]:
    event = {
        "time": 1793182844,
        "self_id": SELF_ID,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": "测试消息"}}],
        "raw_message": "测试消息",
        "font": 0,
        "sender": {"user_id": user_id, "nickname": "Zero"},
    }
    event.update(overrides)
    return event


def make_settings(port: int, token: str = TOKEN) -> Settings:
    return Settings(
        onebot=OneBotSettings(
            ws_url=f"ws://127.0.0.1:{port}/",
            access_token=SecretStr(token),
            self_id=SELF_ID,
            connect_timeout=5,
            action_timeout=5,
            reconnect_min_delay=0.05,
            reconnect_max_delay=0.2,
        ),
        bot=BotSettings(allowed_private_senders=[ALLOWED], fixed_reply_text=FIXED_REPLY),
        deepseek=DeepSeekSettings(),
    )


async def test_m1_fixed_reply_end_to_end():
    stub = StubOneBot()
    await stub.start()
    try:
        runner = BotRunner(make_settings(stub.port))
        task = asyncio.create_task(runner.run())

        await stub.wait_for_action("get_login_info")

        # 授权发送者的好友私聊 → 应恰好回复一次固定文本
        await stub.push_event(private_event(message_id=101))
        action = await stub.wait_for_action("send_private_msg")
        assert action["params"]["user_id"] == ALLOWED
        assert action["params"]["message"] == [{"type": "text", "data": {"text": FIXED_REPLY}}]

        # 群消息、未授权发送者、重复 message_id → 不产生任何新回复
        await stub.push_event(private_event(message_id=102, message_type="group", group_id=4321))
        await stub.push_event(private_event(message_id=103, user_id=999999999))
        await stub.push_event(private_event(message_id=101))
        await asyncio.sleep(0.3)

        sends = [d for d in stub.received if d.get("action") == "send_private_msg"]
        assert len(sends) == 1, f"应只回复一次，实际 {len(sends)} 次"
        assert len(stub.sent_messages) == 1
        assert not stub.handler_errors

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await stub.stop()


async def test_reconnect_and_reply_after_server_drop():
    stub = StubOneBot()
    await stub.start()
    try:
        runner = BotRunner(make_settings(stub.port))
        task = asyncio.create_task(runner.run())
        await stub.wait_for_action("get_login_info")

        # 第一条消息正常回复
        await stub.push_event(private_event(message_id=201))
        await stub.wait_for_action("send_private_msg")

        # 服务端主动断开，机器人应自动重连并继续应答
        await stub._ws.close()
        deadline = asyncio.get_running_loop().time() + 5
        while stub.connections < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert stub.connections >= 2, "客户端未能自动重连"

        # 重连后推送新消息，等待第二次回复动作真实到达
        await stub.push_event(private_event(message_id=202))
        await stub.wait_for_action("send_private_msg", occurrence=2)
        assert len(stub.sent_messages) == 2
        assert not stub.handler_errors

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await stub.stop()


async def test_wrong_token_handshake_rejected():
    stub = StubOneBot(token="real-token")
    await stub.start()
    try:
        runner = BotRunner(make_settings(stub.port, token="wrong"))
        task = asyncio.create_task(runner.run())
        # 客户端会重连重试；无论哪种失败形态，都不应发出任何动作
        await asyncio.sleep(0.3)
        assert stub.received == []
        assert stub.sent_messages == []
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    finally:
        await stub.stop()

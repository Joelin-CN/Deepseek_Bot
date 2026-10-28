"""端到端流程测试：对替身 OneBot 服务（StubOneBot）跑完整 BotRunner。

替身服务实现正向 WS 服务端（Universal 角色）：校验 Bearer Token、推
lifecycle 事件、按 echo 回应动作；测试向它推送合成私聊事件，覆盖筛选、
去重、记忆命令、动作结果三分（成功/失败/未知不重发）、重连重验证、
身份不匹配致命错误与 worker 并发上限。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import pytest
from pydantic import SecretStr
from websockets.asyncio.server import serve

from companion.config.settings import BotSettings, DeepSeekSettings, OneBotSettings, Settings
from companion.core.lifecycle import BotRunner
from companion.persistence import Database

SELF_ID = 3666167977
ALLOWED = 844281600
FIXED_REPLY = "收到，QQ 链路已连通。"
TOKEN = "stub-token"


class StubOneBot:
    """扮演 SnowLuma 的替身服务。"""

    def __init__(
        self,
        token: str = TOKEN,
        *,
        login_uin: int = SELF_ID,
        login_reply_delay: float = 0.0,
        send_reply_delay: float = 0.0,
        drop_sends_without_reply: bool = False,
    ):
        self.token = token
        self.login_uin = login_uin
        self.login_reply_delay = login_reply_delay
        self.send_reply_delay = send_reply_delay
        self.drop_sends_without_reply = drop_sends_without_reply
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
                    "time": int(time.time()),
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
                action = data.get("action")
                if action == "send_private_msg" and self.drop_sends_without_reply:
                    # 服务端已执行发送但不回执，直接断线（只影响一次）
                    self.sent_messages.append(data["params"])
                    self.drop_sends_without_reply = False
                    await ws.close()
                    return
                if action == "get_login_info" and self.login_reply_delay:
                    await asyncio.sleep(self.login_reply_delay)
                elif action == "send_private_msg" and self.send_reply_delay:
                    await asyncio.sleep(self.send_reply_delay)
                await self._send(self._respond(data))
        except Exception as exc:  # 记录而不是吞掉，便于断言
            self.handler_errors.append(f"{type(exc).__name__}: {exc}")

    def _respond(self, data: dict[str, Any]) -> dict[str, Any]:
        action = data.get("action")
        echo = data.get("echo")
        if action == "get_login_info":
            payload = {
                "status": "ok",
                "retcode": 0,
                "data": {"user_id": self.login_uin, "nickname": "Biubiu"},
            }
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
        raise AssertionError(f"等待动作 {action} 第 {occurrence} 次出现超时；已收到: {self.received}")

    async def wait_connections(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while self.connections < count and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert self.connections >= count


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


def make_settings(port: int, token: str = TOKEN, **bot_overrides: Any) -> Settings:
    bot = {"allowed_private_senders": [ALLOWED], "owner_qq": ALLOWED, "fixed_reply_text": FIXED_REPLY}
    bot.update(bot_overrides)
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
        bot=BotSettings(**bot),
        deepseek=DeepSeekSettings(),
    )


async def make_db(tmp_path) -> Database:
    return await Database.open(tmp_path / "bot.db")


@contextlib.asynccontextmanager
async def running_bot(settings: Settings, db: Database, responder=None):
    runner = BotRunner(settings, responder, database=db)
    task = asyncio.create_task(runner.run())
    try:
        yield runner, task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def test_m1_fixed_reply_end_to_end(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port), db) as (_, task):
            await stub.wait_for_action("get_login_info")

            await stub.push_event(private_event(message_id=101))
            action = await stub.wait_for_action("send_private_msg")
            assert action["params"]["user_id"] == ALLOWED
            assert action["params"]["message"] == [{"type": "text", "data": {"text": FIXED_REPLY}}]

            await stub.push_event(private_event(message_id=102, message_type="group", group_id=4321))
            await stub.push_event(private_event(message_id=103, user_id=999999999))
            await stub.push_event(private_event(message_id=101))
            await asyncio.sleep(0.3)

            sends = [d for d in stub.received if d.get("action") == "send_private_msg"]
            assert len(sends) == 1, f"应只回复一次，实际 {len(sends)} 次"
            assert not stub.handler_errors

        # 消息状态持久化：101 → sent_ok；群消息/陌生人未记录；重复 101 只记录一次
        row = await db.fetchone(
            "SELECT status, delivery_message_id FROM messages WHERE platform_message_id=101"
        )
        assert row is not None and row[0] == "sent_ok" and row[1] == 9001
        total = await db.fetchone("SELECT COUNT(*) FROM messages")
        assert total[0] == 1
    finally:
        await db.close()
        await stub.stop()


async def test_reconnect_and_reply_after_server_drop(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port), db) as (_, task):
            await stub.wait_for_action("get_login_info")

            await stub.push_event(private_event(message_id=201))
            await stub.wait_for_action("send_private_msg")

            await stub._ws.close()
            await stub.wait_connections(2)
            # 等第二次连接完成身份验证（重连重验证），再推业务消息
            await stub.wait_for_action("get_login_info", occurrence=2)

            await stub.push_event(private_event(message_id=202))
            await stub.wait_for_action("send_private_msg", occurrence=2)
            assert len(stub.sent_messages) == 2
            assert not stub.handler_errors

        for mid, status in ((201, "sent_ok"), (202, "sent_ok")):
            row = await db.fetchone(
                "SELECT status FROM messages WHERE platform_message_id=?", (mid,)
            )
            assert row[0] == status
    finally:
        await db.close()
        await stub.stop()


async def test_wrong_token_handshake_rejected(tmp_path):
    stub = StubOneBot(token="real-token")
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port, token="wrong"), db):
            await asyncio.sleep(0.3)
            assert stub.received == []
            assert stub.sent_messages == []
    finally:
        await db.close()
        await stub.stop()


async def test_send_receipt_lost_marks_unknown_and_no_resend(tmp_path):
    """服务端已执行发送但回执丢失：客户端标记 send_unknown，重连后不补发。"""
    stub = StubOneBot(drop_sends_without_reply=True)
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port), db) as (_, task):
            await stub.wait_for_action("get_login_info")
            await stub.push_event(private_event(message_id=401))
            await stub.wait_for_action("send_private_msg")
            # 服务端执行后立即断线；客户端应得到“结果未知”并等待重连
            await stub.wait_connections(2)
            await stub.wait_for_action("get_login_info", occurrence=2)
            await asyncio.sleep(0.2)

            # 消息 401 的动作在服务端恰好执行一次，没有补发
            sends_401 = [
                d for d in stub.received
                if d.get("action") == "send_private_msg" and d["params"]["user_id"] == ALLOWED
            ]
            assert len(sends_401) == 1

            # 重连验证后，新消息仍正常应答
            await stub.push_event(private_event(message_id=402))
            await stub.wait_for_action("send_private_msg", occurrence=2)
            assert len(stub.sent_messages) == 2

        unknown = await db.fetchone(
            "SELECT status FROM messages WHERE platform_message_id=401"
        )
        ok = await db.fetchone(
            "SELECT status FROM messages WHERE platform_message_id=402"
        )
        assert unknown[0] == "send_unknown"
        assert ok[0] == "sent_ok"
    finally:
        await db.close()
        await stub.stop()


async def test_action_timeout_yields_unknown_and_recovers(tmp_path):
    """send 回包超时 → ActionUnknown（不重发）；迟到的回包被忽略，后续动作正常。"""
    stub = StubOneBot(send_reply_delay=1.5)
    await stub.start()
    db = await make_db(tmp_path)
    settings = make_settings(stub.port)
    settings = settings.model_copy(
        update={"onebot": settings.onebot.model_copy(update={"action_timeout": 0.4})}
    )
    try:
        async with running_bot(settings, db) as (_, task):
            await stub.wait_for_action("get_login_info")  # 登录回包不受 send 延迟影响
            await stub.push_event(private_event(message_id=501))
            await stub.wait_for_action("send_private_msg")  # 动作已到达服务端
            # 等服务端把延迟的回包发完（替身按帧串行处理），再恢复正常速度
            await asyncio.sleep(1.8)

            stub.send_reply_delay = 0.0
            await stub.push_event(private_event(message_id=502))
            await stub.wait_for_action("send_private_msg", occurrence=2)
            await asyncio.sleep(0.2)

        unknown = await db.fetchone(
            "SELECT status FROM messages WHERE platform_message_id=501"
        )
        ok = await db.fetchone(
            "SELECT status FROM messages WHERE platform_message_id=502"
        )
        assert unknown[0] == "send_unknown"
        assert ok[0] == "sent_ok"
        # 服务端两次 send 各执行一次，客户端没有为 501 补发
        assert len(stub.sent_messages) == 2
    finally:
        await db.close()
        await stub.stop()


async def test_worker_pool_bounds_concurrency(tmp_path):
    class SlowResponder:
        def __init__(self):
            self.current = 0
            self.max_concurrent = 0
            self.handled = 0

        async def respond(self, message):
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)
            await asyncio.sleep(0.08)
            self.current -= 1
            self.handled += 1
            return "ok"

    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    slow = SlowResponder()
    try:
        settings = make_settings(stub.port, max_handlers=2)
        async with running_bot(settings, db, responder=slow) as (_, task):
            await stub.wait_for_action("get_login_info")
            for i in range(8):
                await stub.push_event(private_event(message_id=600 + i))
            await stub.wait_for_action("send_private_msg", occurrence=8, timeout=10)
            assert slow.handled == 8
            assert slow.max_concurrent <= 2
            assert not stub.handler_errors
    finally:
        await db.close()
        await stub.stop()


async def test_identity_mismatch_is_fatal(tmp_path):
    stub = StubOneBot(login_uin=111111111)  # 与配置 self_id 不符
    await stub.start()
    db = await make_db(tmp_path)
    try:
        runner = BotRunner(make_settings(stub.port), database=db)
        task = asyncio.create_task(runner.run())
        with pytest.raises(RuntimeError) as excinfo:
            await asyncio.wait_for(task, timeout=5)
        assert "不一致" in str(excinfo.value)
        assert stub.received == [] or all(
            d.get("action") == "get_login_info" for d in stub.received
        )
    finally:
        await db.close()
        await stub.stop()


async def test_events_before_verification_are_dropped(tmp_path):
    """身份验证完成前推送的业务事件不进入业务队列（也不产生回复）。"""
    stub = StubOneBot(login_reply_delay=0.6)
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port), db) as (_, task):
            await stub.wait_connections(1)
            # 验证完成前推送业务消息：应被忽略
            await stub.push_event(private_event(message_id=301))
            await asyncio.sleep(1.0)  # 覆盖 0.6s 的验证延迟，验证此刻已完成
            assert not any(d.get("action") == "send_private_msg" for d in stub.received)

            # 验证通过后新消息正常应答
            await stub.push_event(private_event(message_id=302))
            await stub.wait_for_action("send_private_msg")
            assert len(stub.sent_messages) == 1

        rows = await db.fetchall("SELECT platform_message_id FROM messages")
        assert [r[0] for r in rows] == [302]  # 301 未进入业务处理
    finally:
        await db.close()
        await stub.stop()


async def test_memory_command_flow_end_to_end(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(make_settings(stub.port), db) as (_, task):
            await stub.wait_for_action("get_login_info")

            await stub.push_event(private_event(message_id=701, message=[{"type": "text", "data": {"text": "记住：我喜欢蓝色"}}]))
            action = await stub.wait_for_action("send_private_msg")
            assert action["params"]["message"][0]["data"]["text"].startswith("已记住（#1）")

            await stub.push_event(private_event(message_id=702, message=[{"type": "text", "data": {"text": "查看记忆"}}]))
            action2 = await stub.wait_for_action("send_private_msg", occurrence=2)
            assert "#1" in action2["params"]["message"][0]["data"]["text"]
            assert "我喜欢蓝色" in action2["params"]["message"][0]["data"]["text"]

            await stub.push_event(private_event(message_id=703, message=[{"type": "text", "data": {"text": "忘记 #1"}}]))
            action3 = await stub.wait_for_action("send_private_msg", occurrence=3)
            assert action3["params"]["message"][0]["data"]["text"] == "已忘记 #1。"

            await stub.push_event(private_event(message_id=704, message=[{"type": "text", "data": {"text": "查看记忆"}}]))
            action4 = await stub.wait_for_action("send_private_msg", occurrence=4)
            assert "当前没有记住的内容" in action4["params"]["message"][0]["data"]["text"]

        # 记忆命令也走消息状态机
        statuses = await db.fetchall(
            "SELECT platform_message_id, status FROM messages ORDER BY id"
        )
        assert all(s == "sent_ok" for _, s in statuses)
        assert len(statuses) == 4
    finally:
        await db.close()
        await stub.stop()

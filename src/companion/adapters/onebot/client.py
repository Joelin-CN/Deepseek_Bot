"""Forward-WebSocket client for an OneBot v11 implementation (SnowLuma).

一条 Universal 角色连接同时承载事件推送和动作回包。接收循环不做任何
阻塞处理：带 pending ``echo`` 的帧直接解决对应 Future，其余帧进入事件
队列，由独立的分发任务消费；出站帧统一经写者任务发送，避免并发写
同一个连接。断线后按指数退避重连。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

import websockets

from companion.config.settings import OneBotSettings

logger = logging.getLogger(__name__)


class ActionError(Exception):
    """OneBot 动作调用失败（非零 retcode、超时或连接断开）。"""

    def __init__(self, action: str, detail: str):
        super().__init__(f"action {action} 失败: {detail}")
        self.action = action
        self.detail = detail


class OneBotClient:
    def __init__(self, settings: OneBotSettings):
        self._settings = settings
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._send_queue: asyncio.Queue[str] = asyncio.Queue()
        self._echo_counter = itertools.count(1)
        self._connected = asyncio.Event()
        self._ever_connected = False
        self._closed = False
        self._connection_task: asyncio.Task[None] | None = None

    @property
    def event_queue(self) -> asyncio.Queue[dict[str, Any]]:
        return self._event_queue

    async def start(self) -> None:
        self._connection_task = asyncio.create_task(self._run(), name="onebot-connection")

    async def stop(self) -> None:
        self._closed = True
        if self._connection_task is not None:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        self._fail_pending(ActionError("stop", "client stopped"))

    async def wait_connected(self, timeout: float | None = None) -> None:
        if timeout is None:
            await self._connected.wait()
        else:
            await asyncio.wait_for(self._connected.wait(), timeout)

    async def _run(self) -> None:
        delay = self._settings.reconnect_min_delay
        headers = {"Authorization": f"Bearer {self._settings.access_token.get_secret_value()}"}
        while not self._closed:
            try:
                async with websockets.connect(
                    self._settings.ws_url,
                    additional_headers=headers,
                    open_timeout=self._settings.connect_timeout,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("已连接 OneBot 服务 %s", self._settings.ws_url)
                    self._ever_connected = True
                    self._connected.set()
                    delay = self._settings.reconnect_min_delay
                    writer = asyncio.create_task(self._writer_loop(ws), name="onebot-writer")
                    try:
                        async for frame in ws:
                            self._route_frame(frame)
                    finally:
                        writer.cancel()
                        self._connected.clear()
                        self._fail_pending(ActionError("call", "connection closed"))
                        logger.warning(
                            "OneBot 连接断开: close_code=%s reason=%s",
                            getattr(ws, "close_code", None),
                            getattr(ws, "close_reason", None),
                        )
            except asyncio.CancelledError:
                raise
            except websockets.InvalidStatus as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (401, 403):
                    raise RuntimeError(
                        f"OneBot 服务拒绝连接（HTTP {status}）：请检查 config/local.json 中 "
                        "onebot.access_token 是否与 SnowLuma 的 wsServers accessToken 一致"
                    ) from exc
                logger.warning("OneBot 握手失败（HTTP %s）", status)
            except Exception as exc:  # 任何传输层错误都进入重连
                logger.warning("OneBot 连接失败: %s", exc)
            if self._closed:
                break
            if not self._ever_connected:
                logger.warning(
                    "尚未成功连接过 OneBot 服务，请确认 SnowLuma 已运行且地址端口正确；"
                    "若握手后被立即断开，通常是 access_token 不匹配"
                )
            logger.info("将在 %.1f 秒后重连", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._settings.reconnect_max_delay)

    async def _writer_loop(self, ws: Any) -> None:
        while True:
            frame = await self._send_queue.get()
            logger.debug("writer 发送帧 ws=%s: %.120s", id(ws), frame)
            await ws.send(frame)

    def _route_frame(self, frame: str | bytes) -> None:
        """分发单个入站帧；只做查表和入队，绝不阻塞接收循环。"""
        try:
            data = json.loads(frame)
        except (TypeError, ValueError):
            logger.warning("丢弃无法解析的帧: %r", str(frame)[:200])
            return
        echo = data.get("echo")
        if isinstance(echo, str) and echo in self._pending:
            future = self._pending[echo]
            if not future.done():
                future.set_result(data)
            return
        self._event_queue.put_nowait(data)

    def _fail_pending(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def call_action(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用 OneBot 动作并等待 echo 回包；失败抛 ActionError。"""
        try:
            await self.wait_connected(timeout=self._settings.connect_timeout)
        except asyncio.TimeoutError as exc:
            raise ActionError(action, "等待连接超时") from exc
        echo = f"echo-{next(self._echo_counter)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        frame = json.dumps({"action": action, "params": params or {}, "echo": echo}, ensure_ascii=False)
        logger.debug("call_action 入队 %s echo=%s connected=%s", action, echo, self._connected.is_set())
        await self._send_queue.put(frame)
        try:
            result = await asyncio.wait_for(future, self._settings.action_timeout)
        except asyncio.TimeoutError as exc:
            raise ActionError(action, f"等待回包超时（>{self._settings.action_timeout:.0f}s）") from exc
        finally:
            self._pending.pop(echo, None)
        if result.get("status") == "ok" and result.get("retcode") == 0:
            data = result.get("data")
            return data if isinstance(data, dict) else {}
        raise ActionError(
            action,
            f"retcode={result.get('retcode')} status={result.get('status')} "
            f"message={result.get('message') or result.get('wording')}",
        )

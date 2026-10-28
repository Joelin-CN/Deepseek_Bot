"""Forward-WebSocket client for an OneBot v11 implementation (SnowLuma).

一条 Universal 角色连接同时承载事件推送和动作回包。接收循环不做任何
阻塞处理：带 pending ``echo`` 的帧直接解决对应 Future，其余帧在**身份
验证通过后**进入有界事件队列，由独立的分发 worker 消费；出站帧统一经
写者任务发送，且写者会跳过已超时/失效的动作，失效连接中未发送的旧
动作不会在重连后意外发出。

动作结果三分：
- 成功：``call_action`` 正常返回 data（retcode=0）。
- ``ActionFailed``：服务端明确返回失败。
- ``ActionUnknown``：回执未知（等待超时或连接断开）——消息可能已送达，
  调用方不得盲目重发。
- ``ActionNotSent``：动作未写出（连接不可用或已停止），可安全重试。
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
    """OneBot 动作调用未成功。"""

    def __init__(self, action: str, detail: str):
        super().__init__(f"action {action} 失败: {detail}")
        self.action = action
        self.detail = detail


class ActionFailed(ActionError):
    """服务端明确返回失败（retcode 非 0）。"""


class ActionUnknown(ActionError):
    """回执未知：消息可能已送达，不得自动重发。"""


class ActionNotSent(ActionError):
    """动作未写出连接，可安全重试。"""


class OneBotClient:
    def __init__(self, settings: OneBotSettings, event_queue_size: int = 1000):
        self._settings = settings
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=event_queue_size)
        self._send_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._echo_counter = itertools.count(1)
        self._connected = asyncio.Event()
        self._verified = asyncio.Event()
        self._identity: dict[str, Any] | None = None
        self._closed = False
        self._ws: Any = None
        self._connection_task: asyncio.Task[None] | None = None

    @property
    def event_queue(self) -> asyncio.Queue[dict[str, Any]]:
        return self._event_queue

    @property
    def verified(self) -> bool:
        return self._verified.is_set()

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
            except RuntimeError:
                pass
        self._fail_pending(ActionNotSent("stop", "客户端已停止，动作未发送"))

    async def wait_ready(self, timeout: float) -> dict[str, Any] | None:
        """等待当前连接完成身份验证并返回登录信息。

        连接任务若以致命错误结束（如 self_id 不匹配），该错误在此抛出。
        """
        if self._connection_task is None:
            raise RuntimeError("客户端尚未 start()")
        waiter = asyncio.create_task(self._verified.wait())
        try:
            done, _ = await asyncio.wait(
                {waiter, self._connection_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=timeout,
            )
        finally:
            waiter.cancel()
        if self._connection_task in done:
            self._connection_task.result()  # 致命错误在此抛出；正常结束返回 None
        if waiter in done and self._verified.is_set():
            return dict(self._identity or {})
        raise TimeoutError(f"等待 OneBot 身份验证超时（>{timeout:.0f}s）")

    async def _run(self) -> None:
        delay = self._settings.reconnect_min_delay
        headers = {"Authorization": f"Bearer {self._settings.access_token.get_secret_value()}"}
        while not self._closed:
            verify: asyncio.Task[None] | None = None
            try:
                async with websockets.connect(
                    self._settings.ws_url,
                    additional_headers=headers,
                    open_timeout=self._settings.connect_timeout,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("已连接 OneBot 服务 %s，等待身份验证", self._settings.ws_url)
                    self._ws = ws
                    self._connected.set()
                    self._verified.clear()
                    self._identity = None
                    delay = self._settings.reconnect_min_delay
                    writer = asyncio.create_task(self._writer_loop(ws), name="onebot-writer")
                    verify = asyncio.create_task(self._verify_identity(), name="onebot-verify")
                    try:
                        async for frame in ws:
                            self._route_frame(frame)
                    finally:
                        writer.cancel()
                        self._ws = None
                        self._connected.clear()
                        self._verified.clear()
                        # 先释放 pending 回包（验证任务可能正在等待 echo）
                        self._fail_pending(
                            ActionUnknown("call", "连接断开，回执结果未知")
                        )
                        verify_error: BaseException | None = None
                        if verify is not None:
                            try:
                                await verify  # 不取消：让致命错误得以冒出
                            except (asyncio.CancelledError, ActionError):
                                pass
                            except RuntimeError as exc:  # self_id 不匹配为致命错误
                                verify_error = exc
                        logger.warning(
                            "OneBot 连接断开: close_code=%s reason=%s",
                            getattr(ws, "close_code", None),
                            getattr(ws, "close_reason", None),
                        )
                        if verify_error is not None:
                            raise verify_error
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
            except RuntimeError:
                raise  # 致命配置错误（如身份不匹配）不进入重连
            except Exception as exc:  # 其他传输层错误进入重连
                logger.warning("OneBot 连接失败: %s", exc)
            if self._closed:
                break
            logger.info("将在 %.1f 秒后重连", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._settings.reconnect_max_delay)

    async def _verify_identity(self) -> None:
        """每次连接成功后重新确认机器人身份；未通过前事件不进入业务队列。"""
        try:
            data = await self._request("get_login_info", {})
        except ActionError as exc:
            logger.warning("身份验证未完成（%s），关闭本连接待重连", exc)
            if self._ws is not None:
                await self._ws.close()
            return
        login_uin = data.get("user_id")
        if login_uin != self._settings.self_id:
            if self._ws is not None:
                await self._ws.close()  # 结束接收循环，让主循环取到致命错误
            raise RuntimeError(
                f"OneBot 登录账号 ({login_uin}) 与配置的 onebot.self_id "
                f"({self._settings.self_id}) 不一致，请检查 config/local.json"
            )
        self._identity = data
        self._verified.set()
        logger.info("连接已验证: uin=%s nickname=%s", login_uin, data.get("nickname"))

    async def _writer_loop(self, ws: Any) -> None:
        while True:
            echo, frame = await self._send_queue.get()
            if echo not in self._pending:
                # 已超时/失效的旧动作不允许在（重连后的）新连接上补发
                logger.info("跳过已失效动作 echo=%s", echo)
                continue
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
        if not self._verified.is_set():
            logger.debug("身份验证前收到事件，忽略: %s", str(data)[:120])
            return
        if self._event_queue.full():
            logger.warning("事件队列已满（%d），丢弃新事件以保护接收循环", self._event_queue.maxsize)
            return
        self._event_queue.put_nowait(data)

    def _fail_pending(self, error: ActionError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def call_action(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用 OneBot 动作并等待 echo 回包。

        成功返回 data；失败抛 ActionFailed / ActionUnknown / ActionNotSent。
        """
        return await self._request(action, params, wait_connect=True)

    async def _request(
        self, action: str, params: dict[str, Any] | None, wait_connect: bool = False
    ) -> dict[str, Any]:
        if wait_connect:
            try:
                await asyncio.wait_for(self._connected.wait(), self._settings.connect_timeout)
            except asyncio.TimeoutError as exc:
                raise ActionNotSent(action, "连接不可用，动作未发送") from exc
        elif not self._connected.is_set():
            raise ActionNotSent(action, "连接不可用，动作未发送")
        echo = f"echo-{next(self._echo_counter)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        frame = json.dumps({"action": action, "params": params or {}, "echo": echo}, ensure_ascii=False)
        logger.debug("call_action 入队 %s echo=%s connected=%s", action, echo, self._connected.is_set())
        await self._send_queue.put((echo, frame))
        try:
            result = await asyncio.wait_for(future, self._settings.action_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(echo, None)
            raise ActionUnknown(
                action, f"等待回包超时（>{self._settings.action_timeout:.1f}s），结果未知"
            ) from exc
        finally:
            self._pending.pop(echo, None)
        if result.get("status") == "ok" and result.get("retcode") == 0:
            data = result.get("data")
            return data if isinstance(data, dict) else {}
        raise ActionFailed(
            action,
            f"retcode={result.get('retcode')} status={result.get('status')} "
            f"message={result.get('message') or result.get('wording')}",
        )

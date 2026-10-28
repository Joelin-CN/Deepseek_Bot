"""生命周期编排。

流程：配置校验（构造前完成）→ 连接 OneBot → 接收事件 → 筛选 → 去重 →
会话内执行 → 发送回复 → 核对动作回包 → 记录日志；取消或退出时统一清理。
"""

from __future__ import annotations

import asyncio
import logging

from companion.adapters.onebot import ActionError, OneBotClient, text_segment
from companion.adapters.onebot.events import parse_private_message
from companion.config.settings import Settings
from companion.core.agent import build_responder
from companion.core.events import InboundMessage
from companion.core.pipeline import FilterChain, Responder
from companion.core.sessions import DedupCache, SessionLocks

logger = logging.getLogger(__name__)


class BotRunner:
    def __init__(self, settings: Settings, responder: Responder | None = None):
        self._settings = settings
        self._client = OneBotClient(settings.onebot)
        self._filters = FilterChain(
            self_id=settings.onebot.self_id,
            allowed_senders=set(settings.bot.allowed_private_senders),
        )
        self._dedup = DedupCache(settings.bot.dedup_cache_size)
        self._locks = SessionLocks()
        self._responder = responder or build_responder(settings)
        self._dispatch_task: asyncio.Task[None] | None = None
        self._handler_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        await self._client.start()
        try:
            login = await self._client.call_action("get_login_info")
            login_uin = login.get("user_id")
            if login_uin != self._settings.onebot.self_id:
                raise RuntimeError(
                    f"OneBot 登录账号 ({login_uin}) 与配置的 onebot.self_id "
                    f"({self._settings.onebot.self_id}) 不一致，请检查 config/local.json"
                )
            logger.info(
                "机器人身份确认: uin=%s nickname=%s，等待私聊消息…",
                login_uin,
                login.get("nickname"),
            )
            self._dispatch_task = asyncio.create_task(self._dispatch_loop(), name="dispatcher")
            await self._dispatch_task
        finally:
            await self._stop()

    async def _dispatch_loop(self) -> None:
        queue = self._client.event_queue
        while True:
            raw = await queue.get()
            if not self._filters.accepts(raw):
                continue
            inbound = parse_private_message(raw)
            if inbound is None:
                continue
            if self._dedup.seen_before(inbound.message_id):
                logger.info(
                    "重复消息已忽略: message_id=%s user_id=%s", inbound.message_id, inbound.user_id
                )
                continue
            task = asyncio.create_task(
                self._handle(inbound), name=f"handle-{inbound.user_id}-{inbound.message_id}"
            )
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)

    async def _handle(self, inbound: InboundMessage) -> None:
        async with self._locks.for_key(inbound.user_id):
            try:
                reply_text = await self._responder.respond(inbound)
                data = await self._client.call_action(
                    "send_private_msg",
                    {"user_id": inbound.user_id, "message": text_segment(reply_text)},
                )
            except ActionError as exc:
                logger.error(
                    "回复发送失败: user_id=%s message_id=%s error=%s",
                    inbound.user_id,
                    inbound.message_id,
                    exc,
                )
                return
            logger.info(
                "已回复: user_id=%s 入站message_id=%s 回执message_id=%s 回复长度=%d",
                inbound.user_id,
                inbound.message_id,
                data.get("message_id"),
                len(reply_text),
            )
            logger.debug("回复内容: %s", reply_text)

    async def _stop(self) -> None:
        for task in list(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        await self._client.stop()
        close = getattr(self._responder, "close", None)
        if close is not None:
            await close()
        logger.info("已退出，清理完成")

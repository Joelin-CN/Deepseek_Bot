"""生命周期编排。

流程：配置校验（构造前完成）→ 打开持久化 → 连接 OneBot 并等待身份
验证 → worker 池消费事件队列 → 筛选 → 去重 → 会话内执行（命令或
模型）→ 发送回复并记录送达状态 → 取消或退出时统一清理。

worker 池有上限，单条消息异常只记日志；同一会话经 SessionKey 锁串
行，不同会话并发。
"""

from __future__ import annotations

import asyncio
import logging

from companion.adapters.onebot import (
    ActionFailed,
    ActionNotSent,
    ActionUnknown,
    OneBotClient,
    text_segment,
)
from companion.adapters.onebot.events import parse_message
from companion.config.settings import Settings
from companion.core.agent import build_responder
from companion.core.events import InboundMessage
from companion.core.permissions import RunContext, actor_for
from companion.core.pipeline import FilterChain, MemoryCommandRouter, Responder
from companion.core.sessions import SessionManager
from companion.core.sessions.store import MessageStore, UsageStore
from companion.memory import MemoryService
from companion.persistence import Database

logger = logging.getLogger(__name__)


class BotRunner:
    def __init__(
        self,
        settings: Settings,
        responder: Responder | None = None,
        *,
        database: Database | None = None,
        memory_service: MemoryService | None = None,
    ):
        self._settings = settings
        self._external_responder = responder
        self._database = database
        self._owns_database = database is None
        self._external_memory = memory_service
        self._sessions = SessionManager(settings.bot.dedup_cache_size)
        self._client = OneBotClient(
            settings.onebot, event_queue_size=settings.bot.event_queue_size
        )
        self._filters = FilterChain(
            self_id=settings.onebot.self_id,
            allowed_senders=set(settings.bot.allowed_private_senders),
        )
        self._responder: Responder | None = responder
        self._store: MessageStore | None = None
        self._usage: UsageStore | None = None
        self._commands: MemoryCommandRouter | None = None
        self._workers: list[asyncio.Task[None]] = []

    async def run(self) -> None:
        if self._database is None:
            assert self._settings.data_dir_resolved is not None
            self._database = await Database.open(self._settings.data_dir_resolved / "bot.db")
        self._store = MessageStore(self._database)
        self._usage = UsageStore(self._database)
        memory = self._external_memory or MemoryService.from_database(self._database)
        self._commands = MemoryCommandRouter(memory)
        if self._external_responder is None:
            self._responder = build_responder(
                self._settings,
                sessions=self._sessions,
                memory=memory,
                usage=self._usage,
            )

        await self._client.start()
        try:
            ready_timeout = (
                self._settings.onebot.connect_timeout + self._settings.onebot.action_timeout
            )
            identity = await self._client.wait_ready(ready_timeout)
            logger.info(
                "机器人身份确认: uin=%s nickname=%s，worker=%d，等待私聊消息…",
                identity.get("user_id") if identity else "?",
                identity.get("nickname") if identity else "?",
                self._settings.bot.max_handlers,
            )
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"dispatch-{index}")
                for index in range(self._settings.bot.max_handlers)
            ]
            await asyncio.gather(*self._workers)
        finally:
            await self._stop()

    async def _worker(self, index: int) -> None:
        queue = self._client.event_queue
        while True:
            raw = await queue.get()
            try:
                await self._process(raw)
            except Exception:
                logger.exception("消息处理异常（worker %d）", index)

    async def _process(self, raw: dict) -> None:
        accepted, reason = self._filters.classify(raw)
        if not accepted:
            logger.debug("过滤事件（%s）: %s", reason, str(raw)[:200])
            return
        message = parse_message(raw)
        if message is None:
            return
        if self._sessions.seen(message):
            logger.info(
                "重复消息已忽略: message_id=%s session=%s",
                message.message_id,
                message.session.as_scope(),
            )
            return
        actor = actor_for(
            message.session.platform,
            message.sender_id,
            message.sender_nickname,
            owner_id=self._settings.bot.owner_qq,
            allowed_senders=set(self._settings.bot.allowed_private_senders),
        )
        ctx = RunContext(session=message.session, actor=actor)
        assert self._store is not None
        row_id = await self._store.record_inbound(message)
        async with self._sessions.lock(message.session):
            reply = await self._compose_reply(ctx, message)
            await self._store.mark_generated(row_id, reply)
            await self._send_reply(message, reply, row_id)

    async def _compose_reply(self, ctx: RunContext, message: InboundMessage) -> str:
        assert self._commands is not None and self._responder is not None
        command_reply = await self._commands.try_handle(ctx, message)
        if command_reply is not None:
            return command_reply
        return await self._responder.respond(message)

    async def _send_reply(self, message: InboundMessage, reply: str, row_id: int) -> None:
        assert self._store is not None
        params = {"user_id": message.session.chat_target, "message": text_segment(reply)}
        try:
            data = await self._client.call_action("send_private_msg", params)
        except ActionFailed as exc:
            await self._store.mark_delivery(row_id, "send_failed")
            logger.error(
                "回复发送失败: session=%s message_id=%s error=%s",
                message.session.as_scope(), message.message_id, exc,
            )
        except ActionUnknown as exc:
            await self._store.mark_delivery(row_id, "send_unknown")
            logger.warning(
                "回复发送结果未知（不自动重发）: session=%s message_id=%s error=%s",
                message.session.as_scope(), message.message_id, exc,
            )
        except ActionNotSent as exc:
            await self._store.mark_delivery(row_id, "send_failed")
            logger.warning(
                "回复未发出: session=%s message_id=%s error=%s",
                message.session.as_scope(), message.message_id, exc,
            )
        else:
            await self._store.mark_delivery(row_id, "sent_ok", data.get("message_id"))
            logger.info(
                "已回复: session=%s 入站message_id=%s 回执message_id=%s 回复长度=%d",
                message.session.as_scope(), message.message_id, data.get("message_id"), len(reply),
            )

    async def _stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        close = getattr(self._responder, "close", None)
        if close is not None:
            await close()
        await self._client.stop()
        if self._owns_database and self._database is not None:
            await self._database.close()
        logger.info("已退出，清理完成")

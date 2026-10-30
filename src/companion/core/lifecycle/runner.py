"""生命周期编排。

流程：配置校验（构造前完成）→ 打开持久化 → 插件宿主发现/加载/按状态
启用 → 连接 OneBot 并等待身份验证 → worker 池消费事件队列 → 筛选 →
去重 → 会话内执行（插件阶段 → 记忆命令或模型）→ 发送回复并记录送达
状态 → **送达成功后才提交对话历史** → 取消或退出时统一清理（先停插
件再关连接）。

worker 池有上限，单条消息异常只记日志；同一会话经 SessionKey 锁串
行，不同会话并发。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

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
from companion.core.tasks import TaskManager
from companion.core.permissions import Role, RunContext, actor_for
from companion.core.pipeline import FilterChain, MemoryCommandRouter, Responder
from companion.core.sessions import SessionManager
from companion.core.sessions.store import MessageStore, UsageStore
from companion.core.tools import ToolExecutionService, ToolRegistry
from companion.memory import MemoryService
from companion.persistence import Database
from companion.plugin_system import PluginHost

logger = logging.getLogger(__name__)


class BotRunner:
    def __init__(
        self,
        settings: Settings,
        responder: Responder | None = None,
        *,
        database: Database | None = None,
        memory_service: MemoryService | None = None,
        sessions: SessionManager | None = None,
    ):
        self._settings = settings
        self._external_responder = responder
        self._database = database
        self._owns_database = database is None
        self._external_memory = memory_service
        history_cap = settings.deepseek.max_history_messages
        self._sessions = sessions or SessionManager(
            settings.bot.dedup_cache_size,
            max_stored_messages=2 * history_cap if history_cap > 0 else 0,
        )
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
        self._tools = ToolRegistry()
        self._plugin_host: PluginHost | None = None
        self._task_manager = TaskManager()
        self._tool_service: ToolExecutionService | None = None
        self._workers: list[asyncio.Task[None]] = []

    async def run(self) -> None:
        if self._database is None:
            assert self._settings.data_dir_resolved is not None
            self._database = await Database.open(self._settings.data_dir_resolved / "bot.db")
        self._store = MessageStore(self._database)
        self._usage = UsageStore(self._database)
        memory = self._external_memory or MemoryService.from_database(self._database)
        self._commands = MemoryCommandRouter(
            memory, on_change=self._sessions.clear_history
        )
        self._sessions.set_history_loader(self._make_history_loader())
        self._plugin_host = PluginHost(
            roots=self._plugin_roots(),
            db=self._database,
            tools=self._tools,
            data_dir=self._settings.data_dir_resolved or Path("data"),
            instance_settings={
                plugin_id: {"enabled": entry.enabled, "config": entry.config}
                for plugin_id, entry in self._settings.plugins.instances.items()
            },
        )
        await self._plugin_host.startup()
        self._tool_service = ToolExecutionService(
            registry=self._tools,
            is_owner_active=self._plugin_host.is_plugin_active,
            tool_timeout=self._settings.tools.tool_timeout,
            max_result_chars=self._settings.tools.max_tool_result_chars,
            cleanup_sink=self._task_manager.attach_cleanup,
        )
        if self._external_responder is None:
            self._responder = build_responder(
                self._settings,
                sessions=self._sessions,
                memory=memory,
                usage=self._usage,
                tools=self._tool_service,
                task_manager=self._task_manager,
                notice_sender=self._send_notice,
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

    def _plugin_roots(self) -> list[Path]:
        """插件根目录：优先用 load_settings 解析好的列表；直接构造的
        Settings（测试）退回本文件所在仓库的 plugins/。"""
        if self._settings.plugin_roots_resolved:
            return list(self._settings.plugin_roots_resolved)
        return [Path(__file__).resolve().parents[4] / "plugins"]

    async def _send_notice(self, message: InboundMessage, text: str) -> None:
        """受理提示等过程消息：直接发送，不进入消息状态机与历史。"""
        params = {"user_id": message.session.chat_target, "message": text_segment(text)}
        try:
            await self._client.call_action("send_private_msg", params)
        except (ActionFailed, ActionUnknown, ActionNotSent) as exc:
            logger.warning("过程提示发送失败（不影响任务）: %s", exc)

    def _handle_task_control(self, ctx: RunContext, text: str) -> str:
        """任务控制命令（/task）：在会话锁之外处理，长任务期间也可响应。"""
        if ctx.actor.role != Role.OWNER:
            logger.info("非主人尝试任务控制: user=%s", ctx.actor.user_id)
            return "任务管理仅限主人执行。"
        parts = text.split()
        usage = "用法：/task list | /task status <ID> | /task cancel <ID>"
        if len(parts) < 2:
            return usage
        sub = parts[1].lower()
        if sub == "list":
            tasks = self._task_manager.list_tasks()
            if not tasks:
                return "当前没有任务记录。"
            lines = [f"任务（运行中 {sum(1 for t in tasks if t.state.value == 'RUNNING')} 个，共 {len(tasks)} 条）："]
            lines.extend(f"- {t.one_line()}" for t in tasks[:15])
            return "\n".join(lines)
        if sub in {"status", "cancel"}:
            if len(parts) < 3:
                return f"缺少任务 ID。{usage}"
            task_id = parts[2].strip().strip("<>")  # 容错：按提示复制时常见尖括号
            if sub == "status":
                record = self._task_manager.get(task_id)
                if record is None:
                    return f"未找到任务 {task_id}。"
                return record.one_line() + (f"\n说明：{record.note}" if record.note else "")
            ok, msg = self._task_manager.cancel(task_id, ctx)
            prefix = "取消请求已发出" if ok else "取消失败"
            return f"{prefix}：{msg}"
        return usage

    def _make_history_loader(self):
        store = self._store

        async def loader(session, limit: int) -> list[dict[str, str]]:
            pairs = await store.delivered_turns(session.as_scope(), max(1, limit // 2))
            turns: list[dict[str, str]] = []
            for user_text, assistant_text in reversed(pairs):  # 库中新→旧，反转为时间正序
                turns.append({"role": "user", "content": user_text})
                turns.append({"role": "assistant", "content": assistant_text})
            return turns

        return loader

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
        assert self._store is not None and self._commands is not None and self._responder is not None
        row_id = await self._store.record_inbound(message)
        text = message.text.strip()
        if text.startswith("/task") and text.split(None, 1)[0] == "/task":
            # 任务控制在会话锁之外执行：长任务持有锁时仍可取消
            reply = self._handle_task_control(ctx, text)
            await self._store.mark_generated(row_id, reply, conversational=False)
            await self._send_reply(message, reply, row_id)
            return
        async with self._sessions.lock(message.session):
            plugin_result = None
            if self._plugin_host is not None:
                plugin_result = await self._plugin_host.handle_message(ctx, message)
            if plugin_result is not None and plugin_result.consumed:
                if plugin_result.text is not None:
                    reply = plugin_result.text
                    await self._store.mark_generated(row_id, reply, conversational=False)
                    await self._send_reply(message, reply, row_id)
                else:  # 插件消费但无需回复
                    await self._store.mark_generated(row_id, "", conversational=False)
                    await self._store.mark_delivery(row_id, "consumed")
                    logger.info(
                        "消息被插件消费（无回复）: session=%s message_id=%s",
                        message.session.as_scope(), message.message_id,
                    )
                return
            command_reply = await self._commands.try_handle(ctx, message)
            if command_reply is not None:
                reply = command_reply
                await self._store.mark_generated(row_id, reply, conversational=False)
            else:
                reply = await self._responder.respond(message, ctx)
                await self._store.mark_generated(row_id, reply)
            await self._send_reply(message, reply, row_id)

    async def _send_reply(self, message: InboundMessage, reply: str, row_id: int) -> None:
        assert self._store is not None and self._responder is not None
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
                "回复发送结果未知（不自动重发，不进入对话历史）: session=%s message_id=%s error=%s",
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
            commit = getattr(self._responder, "commit", None)
            if commit is not None:
                await commit(message, reply)  # 只有确认送达的回复进入对话历史
            logger.info(
                "已回复: session=%s 入站message_id=%s 回执message_id=%s 回复长度=%d",
                message.session.as_scope(), message.message_id, data.get("message_id"), len(reply),
            )

    async def _stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        await self._task_manager.cancel_all(note="进程退出")  # 先让运行中任务取消并执行清理
        if self._plugin_host is not None:
            await self._plugin_host.shutdown()  # 再停插件（可能仍在写 KV / 清理浏览器资源）
            self._plugin_host = None
        close = getattr(self._responder, "close", None)
        if close is not None:
            await close()
        await self._client.stop()
        if self._owns_database and self._database is not None:
            await self._database.close()
        logger.info("已退出，清理完成")

"""模型应答器：总预算内的请求组装、工具调用循环、送达后才提交历史。

短期上下文按 SessionKey 维护、内存中有界、可从持久层恢复已送达轮
次——这不是长期记忆服务。长期记忆经注入的 memory 服务在每次组装请
求时实时检索（无长驻缓存）。模型调用失败时给用户返回明确的中文提
示，原始错误进日志。

M5 起的对话内工具循环：

- 普通聊天（无可见工具）保持单次直答，行为与 M4 前一致；
- 有可见工具时进入循环：请求 → tool_calls → 逐个校验执行 → 按对应
  tool_call_id 回传 → 再请求，直到最终答复或达到限制；
- 每轮模型请求都计入 max_input_tokens 预算（含工具定义、调用参数与
  工具结果）并逐轮记录用量；超预算时按完整工具轮次（assistant 调用
  消息 + 其全部 tool 回复）从最旧裁剪，保持配对结构；
- 工具过程是本任务的执行轨迹，不写入长期聊天历史；历史仍只包含实
  际送达的最终答复（工具轮次的 user/assistant 对在提交时只落最终文
  本）。

待提交状态（``_pending``）按会话隔离并绑定 message_id：多个会话并发
时互不覆盖，过期任务的送达回执不会写进别的轮次。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Protocol

from companion.adapters.deepseek import ChatResult, DeepSeekClient, DeepSeekError
from companion.config.settings import DeepSeekSettings, Settings, ToolSettings
from companion.core.agent.context import clip_to_token_budget, estimate_tokens, trim_history
from companion.core.events import InboundMessage, SessionKey
from companion.core.tasks import TaskManager, TaskState
from companion.core.permissions import RunContext
from companion.core.pipeline import FixedReplyResponder, Responder
from companion.core.sessions import SessionManager
from companion.core.tools import ToolExecutionService

logger = logging.getLogger(__name__)

_MEMORY_HEADER = "\n\n以下是本会话的长期记忆（仅限本会话使用）：\n"

_CANCELLED_REPLY = "任务已取消。"
_EMPTY_REPLY = "模型返回了空响应，请稍后重试。"


class MemoryContextProvider(Protocol):
    async def context_slice(self, session: SessionKey, max_items: int) -> list[str]: ...


class UsageSink(Protocol):
    async def record(
        self,
        *,
        session_key: str,
        actor_id: int,
        source: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None: ...


NoticeSender = Callable[[InboundMessage, str], Awaitable[None]]


def _log_usage_failure(task: "asyncio.Task") -> None:
    exc = task.exception()
    if exc is not None:
        logger.error("用量落库失败: %s: %s", type(exc).__name__, exc)


class DeepSeekResponder:
    def __init__(
        self,
        client: DeepSeekClient,
        settings: DeepSeekSettings,
        *,
        sessions: SessionManager | None = None,
        memory: MemoryContextProvider | None = None,
        usage: UsageSink | None = None,
        tools: ToolExecutionService | None = None,
        tool_settings: ToolSettings | None = None,
        task_manager: TaskManager | None = None,
        notice_sender: NoticeSender | None = None,
    ):
        self._client = client
        self._settings = settings
        self._sessions = sessions or SessionManager()
        self._memory = memory
        self._usage = usage
        self._tools = tools
        self._tool_settings = tool_settings or ToolSettings()
        self._task_manager = task_manager
        self._notice = notice_sender
        self._gate = asyncio.Semaphore(settings.model_concurrency)
        # 待提交轮次按会话隔离，绑定 message_id，防并发会话互相覆盖
        self._pending: dict[SessionKey, tuple[int, str]] = {}

    async def respond(self, message: InboundMessage, ctx: RunContext) -> str:
        request_messages = await self._build_request(message)
        tools_payload = self._tools_payload(ctx)
        if not tools_payload:
            return await self._respond_plain(message, request_messages)
        return await self._respond_with_tools(message, ctx, request_messages, tools_payload)

    # ---- 普通聊天：单次直答（与 M4 前一致） ----

    async def _respond_plain(self, message: InboundMessage, request_messages: list[dict[str, Any]]) -> str:
        try:
            async with self._gate:
                result = await self._client.chat(request_messages)
        except DeepSeekError as exc:
            logger.error(
                "DeepSeek 调用失败: kind=%s status=%s session=%s sender=%s detail=%s",
                exc.kind, exc.status_code, message.session.as_scope(), message.sender_id, exc.detail,
            )
            return exc.user_message  # 本地错误提示，不进入对话历史
        self._record_usage_locally(message, result)
        self._set_pending(message, result)
        logger.debug("DeepSeek 回复: session=%s content=%s", message.session.as_scope(), result.content)
        return self._cap_reply(result.content)

    # ---- 工具循环 ----

    async def _respond_with_tools(
        self,
        message: InboundMessage,
        ctx: RunContext,
        request_messages: list[dict[str, Any]],
        tools_payload: list[dict[str, Any]],
    ) -> str:
        assert self._tools is not None
        limits = self._tool_settings
        record = self._task_manager.create(ctx, message) if self._task_manager else None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limits.task_timeout
        msgs = list(request_messages)
        executed: dict[str, Any] = {}  # call_id -> 已执行结果（重复 ID 复用，不重复执行）
        emitted: set[str] = set()
        contents: list[str] = []  # 模型各轮的非空正文（工具轮次可能承载实质内容，不能丢弃）
        rounds = 0
        total_calls = 0
        notice_sent = False

        def _finish(state: TaskState, note: str = "") -> None:
            if record is not None and record.state is TaskState.RUNNING:
                self._task_manager.finish(record.task_id, state, note=note)

        while True:
            if ctx.cancelled:
                _finish(TaskState.CANCELLED, note="控制命令取消")
                return _CANCELLED_REPLY
            if rounds >= limits.max_model_rounds:
                _finish(TaskState.FAILED, note=f"模型轮数达到上限 {limits.max_model_rounds}")
                return self._limit_reply(f"模型轮数达到上限（{limits.max_model_rounds} 轮）")
            if loop.time() > deadline:
                _finish(TaskState.TIMEOUT, note=f"任务超过总时限 {limits.task_timeout:.0f}s")
                return self._limit_reply(f"任务超过总时限（{limits.task_timeout:.0f}s），已停止")
            msgs = self._fit_budget(msgs)

            try:
                async with self._gate:
                    result = await self._client.chat(msgs, tools=tools_payload)
            except DeepSeekError as exc:
                logger.error(
                    "DeepSeek 工具循环调用失败: kind=%s round=%d session=%s detail=%s",
                    exc.kind, rounds + 1, message.session.as_scope(), exc.detail,
                )
                _finish(TaskState.FAILED, note=f"模型调用失败 {exc.kind}")
                if rounds == 0:
                    return exc.user_message
                return (
                    "工具任务中途模型调用失败，任务未完成。"
                    f"已执行 {total_calls} 次工具调用。{exc.user_message}"
                )
            rounds += 1
            if record is not None:
                record.add_round(result.prompt_tokens, result.completion_tokens, result.total_tokens)
            self._record_usage_locally(message, result, source="chat_tools")

            if not result.tool_calls:
                if result.content.strip():
                    contents.append(result.content.strip())
                if not any(contents):
                    _finish(TaskState.FAILED, note="模型返回空响应")
                    return _EMPTY_REPLY
                _finish(TaskState.COMPLETED, note=f"{rounds} 轮 {total_calls} 次工具")
                self._set_pending(message, result)
                return self._cap_reply("\n\n".join(contents))

            if not notice_sent and limits.accept_notice and self._notice is not None:
                notice_sent = True
                try:
                    await self._notice(
                        message,
                        f"已受理工具任务 {ctx.task_id[:8]}，处理中；"
                        f"如需中止请发送 /task cancel {ctx.task_id[:8]}",
                    )
                except Exception:
                    logger.exception("受理提示发送失败（不影响任务）")

            # 保留模型原始 assistant 工具调用消息；ID 缺失时用序号补齐保证可配对。
            # 带调用的轮次也可能携带实质正文（协议允许并存），累积进最终答复。
            if result.content.strip():
                contents.append(result.content.strip())
            raw_calls = []
            for index, call in enumerate(result.tool_calls):
                call_id = call.id or f"missing-{rounds}-{index}"
                raw_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                )
            msgs.append({"role": "assistant", "content": result.content or "", "tool_calls": raw_calls})

            for index, call in enumerate(result.tool_calls):
                call_id = call.id or f"missing-{rounds}-{index}"
                if ctx.cancelled:
                    _finish(TaskState.CANCELLED, note="控制命令取消")
                    return _CANCELLED_REPLY
                if total_calls >= limits.max_tool_calls:
                    outcome_text = json.dumps(
                        {"error": "tool_call_limit", "detail": f"任务工具调用总数达到上限 {limits.max_tool_calls}"},
                        ensure_ascii=False,
                    )
                elif loop.time() > deadline:
                    outcome_text = json.dumps({"error": "task_timeout"}, ensure_ascii=False)
                elif call_id in executed:
                    outcome_text = executed[call_id]  # 重复 ID：复用结果，不重复执行
                else:
                    from companion.core.tools import ToolCall as ServiceToolCall

                    outcome = await self._tools.execute(
                        ServiceToolCall(id=call_id, name=call.name, arguments=call.arguments),
                        ctx,
                        task_id=ctx.task_id,
                    )
                    total_calls += 1
                    if record is not None:
                        record.tool_calls = total_calls
                    outcome_text = json.dumps(outcome.content, ensure_ascii=False, default=str)
                    executed[call_id] = outcome_text
                if call_id not in emitted:  # 同 ID 多次出现只回传一次，保持消息配对
                    msgs.append({"role": "tool", "tool_call_id": call_id, "content": outcome_text})
                    emitted.add(call_id)

        # 循环只能从内部 return（各终态已明确处理）

    def _limit_reply(self, reason: str) -> str:
        return f"工具任务未完成：{reason}。已获取的部分不会作为最终答复，请缩小问题范围后重试。"

    def _fit_budget(self, msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """每轮请求前保证总估算不超 max_input_tokens。

        只裁剪初始 user 消息之后的完整工具轮次（assistant 调用消息 + 其
        全部 tool 回复一起删），保持调用与结果配对；系统提示、原始历史
        与当前用户消息不动（进入循环前已经过 _build_request 预算控制）。
        """
        budget = self._settings.max_input_tokens
        while True:
            total = sum(
                estimate_tokens(str(m.get("content", "")) + json.dumps(m.get("tool_calls", ""), ensure_ascii=False))
                + 4
                for m in msgs
            )
            if total <= budget or len(msgs) <= 3:
                return msgs
            start = next(
                (i for i, m in enumerate(msgs[1:], start=1) if m.get("tool_calls")),
                None,
            )
            if start is None:
                return msgs
            end = start + 1
            while end < len(msgs) and msgs[end].get("role") == "tool":
                end += 1
            logger.info("工具循环超出输入预算，丢弃最旧工具轮次: 消息 %d-%d", start, end - 1)
            del msgs[start:end]

    # ---- 待提交轮次（按会话隔离） ----

    def _set_pending(self, message: InboundMessage, result: ChatResult) -> None:
        self._pending[message.session] = (message.message_id, message.text)

    async def commit(self, message: InboundMessage, delivered_reply: str) -> None:
        """回复确认送达后提交对话轮次；记录的是实际送达文本。"""
        pending = self._pending.get(message.session)
        if pending is None or pending[0] != message.message_id:
            return  # 无待提交或属于已过期的任务，不串写
        del self._pending[message.session]
        self._sessions.append_turn(message.session, pending[1], delivered_reply)

    # ---- 请求组装（与 M4 相同的预算策略） ----

    async def _build_request(self, message: InboundMessage) -> list[dict[str, Any]]:
        budget = self._settings.max_input_tokens
        user_text = message.text
        user_cost = estimate_tokens(user_text)

        memory_items = await self._memory_items(message.session)

        def build_system(items: list[str]) -> str:
            if not items:
                return self._settings.system_prompt
            return self._settings.system_prompt + _MEMORY_HEADER + "\n".join(f"- {item}" for item in items)

        system_content = build_system(memory_items)
        while memory_items and estimate_tokens(system_content) + user_cost > budget:
            memory_items.pop()  # 列表按新→旧排列，pop 丢最旧
            system_content = build_system(memory_items)
        system_message = {"role": "system", "content": system_content}
        system_cost = estimate_tokens(system_content)

        if system_cost + user_cost > budget:
            allowed = max(1, budget - system_cost - estimate_tokens("\n（消息过长，已截断）") - 4)
            user_text = clip_to_token_budget(user_text, allowed) + "\n（消息过长，已截断）"
            logger.warning(
                "当前消息超输入预算，已截断: session=%s 原长度=%d", message.session.as_scope(), len(message.text)
            )

        history = list(await self._sessions.history(message.session))
        history_budget = max(0, budget - system_cost - estimate_tokens(user_text) - 8)
        if self._settings.max_history_tokens > 0:
            history_budget = min(history_budget, self._settings.max_history_tokens)
        trimmed = trim_history(history, self._settings.max_history_messages, history_budget)

        request: list[dict[str, Any]] = [system_message]
        request.extend(trimmed)
        request.append({"role": "user", "content": user_text})
        return request

    async def _memory_items(self, session: SessionKey) -> list[str]:
        if self._memory is None or self._settings.memory_context_items <= 0:
            return []
        return await self._memory.context_slice(session, self._settings.memory_context_items)

    def _tools_payload(self, ctx: RunContext) -> list[dict[str, Any]]:
        if self._tools is None or not self._tool_settings.enable:
            return []
        try:
            return self._tools.tools_payload(ctx)
        except Exception:
            logger.exception("工具列表构建失败，本轮退化为纯聊天")
            return []

    def _record_usage_locally(
        self, message: InboundMessage, result: ChatResult, *, source: str = "chat"
    ) -> None:
        logger.info(
            "DeepSeek 用量: session=%s sender=%s model=%s source=%s prompt_tokens=%d "
            "completion_tokens=%d total_tokens=%d",
            message.session.as_scope(), message.sender_id, result.model, source,
            result.prompt_tokens, result.completion_tokens, result.total_tokens,
        )
        if self._usage is not None:
            self._record_usage_sink(message, result, source)

    def _record_usage_sink(self, message: InboundMessage, result: ChatResult, source: str) -> None:
        record = self._usage.record(
            session_key=message.session.as_scope(),
            actor_id=message.sender_id,
            source=source,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        )
        # UsageSink.record 是协程方法；不阻塞回复链路，异常由事件循环报告
        task = asyncio.ensure_future(record)
        task.add_done_callback(_log_usage_failure)

    def _cap_reply(self, content: str) -> str:
        limit = self._settings.max_reply_chars
        if len(content) <= limit:
            return content
        logger.warning("回复超长（%d 字符），截断至 %d", len(content), limit)
        return content[:limit] + "\n（回复过长，已截断）"

    async def close(self) -> None:
        await self._client.close()


def build_responder(
    settings: Settings,
    *,
    sessions: SessionManager | None = None,
    memory: MemoryContextProvider | None = None,
    usage: UsageSink | None = None,
    tools: ToolExecutionService | None = None,
    task_manager: TaskManager | None = None,
    notice_sender: NoticeSender | None = None,
) -> Responder:
    """按配置构造应答器；deepseek 模式要求 DEEPSEEK_API_KEY 已设置。"""
    if settings.bot.reply_mode != "deepseek":
        return FixedReplyResponder(settings.bot.fixed_reply_text)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("reply_mode 为 deepseek 但 DEEPSEEK_API_KEY 未设置")
    client = DeepSeekClient(settings.deepseek, api_key)
    return DeepSeekResponder(
        client,
        settings.deepseek,
        sessions=sessions,
        memory=memory,
        usage=usage,
        tools=tools,
        tool_settings=settings.tools,
        task_manager=task_manager,
        notice_sender=notice_sender,
    )

"""M2/M3 应答器：组装有界短期上下文与长期记忆，调用 DeepSeek。

短期上下文按 SessionKey 维护、条数与估算 token 双上限、只在内存中，
重启即清空——这不是长期记忆服务。长期记忆通过注入的 memory 服务在
每次组装请求时实时检索（无长驻缓存，删除/修改即时生效）。模型调用
失败时给用户返回明确的中文提示，原始错误进日志。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Protocol

from companion.adapters.deepseek import ChatResult, DeepSeekClient, DeepSeekError
from companion.config.settings import DeepSeekSettings, Settings
from companion.core.agent.context import trim_history
from companion.core.events import InboundMessage, SessionKey
from companion.core.pipeline import FixedReplyResponder, Responder
from companion.core.sessions import SessionManager

logger = logging.getLogger(__name__)


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


class DeepSeekResponder:
    def __init__(
        self,
        client: DeepSeekClient,
        settings: DeepSeekSettings,
        *,
        sessions: SessionManager | None = None,
        memory: MemoryContextProvider | None = None,
        usage: UsageSink | None = None,
    ):
        self._client = client
        self._settings = settings
        self._sessions = sessions or SessionManager()
        self._memory = memory
        self._usage = usage
        self._gate = asyncio.Semaphore(settings.model_concurrency)

    async def respond(self, message: InboundMessage) -> str:
        history = self._sessions.history(message.session)
        system = await self._system_prompt(message.session)
        request_messages: list[dict[str, Any]] = [system]
        request_messages.extend(
            trim_history(history, self._settings.max_history_messages, self._settings.max_history_tokens)
        )
        request_messages.append({"role": "user", "content": message.text})
        try:
            async with self._gate:
                result = await self._client.chat(request_messages)
        except DeepSeekError as exc:
            logger.error(
                "DeepSeek 调用失败: kind=%s status=%s session=%s sender=%s detail=%s",
                exc.kind,
                exc.status_code,
                message.session.as_scope(),
                message.sender_id,
                exc.detail,
            )
            return exc.user_message
        await self._record_usage(message, result)
        history.append({"role": "user", "content": message.text})
        history.append({"role": "assistant", "content": result.content})
        logger.debug("DeepSeek 回复: session=%s content=%s", message.session.as_scope(), result.content)
        return self._cap_reply(result.content)

    async def _system_prompt(self, session: SessionKey) -> dict[str, str]:
        prompt = self._settings.system_prompt
        if self._memory is not None and self._settings.memory_context_items > 0:
            items = await self._memory.context_slice(session, self._settings.memory_context_items)
            if items:
                prompt += "\n\n以下是本会话的长期记忆（仅限本会话使用）：\n" + "\n".join(
                    f"- {item}" for item in items
                )
        return {"role": "system", "content": prompt}

    async def _record_usage(self, message: InboundMessage, result: ChatResult) -> None:
        logger.info(
            "DeepSeek 用量: session=%s sender=%s model=%s prompt_tokens=%d "
            "completion_tokens=%d total_tokens=%d",
            message.session.as_scope(),
            message.sender_id,
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        )
        if self._usage is not None:
            await self._usage.record(
                session_key=message.session.as_scope(),
                actor_id=message.sender_id,
                source="chat",
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
            )

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
) -> Responder:
    """按配置构造应答器；deepseek 模式要求 DEEPSEEK_API_KEY 已设置。"""
    if settings.bot.reply_mode != "deepseek":
        return FixedReplyResponder(settings.bot.fixed_reply_text)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("reply_mode 为 deepseek 但 DEEPSEEK_API_KEY 未设置")
    client = DeepSeekClient(settings.deepseek, api_key)
    return DeepSeekResponder(
        client, settings.deepseek, sessions=sessions, memory=memory, usage=usage
    )

"""M2 应答器：组装有界短期上下文并调用 DeepSeek。

短期上下文按用户维护、有上限、只存在内存中，重启即清空——这不是长期
记忆服务；长期记忆由后续里程碑的 memory 模块负责。模型调用失败时给
用户返回明确的中文提示，原始错误进日志。
"""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import Any

from companion.adapters.deepseek import ChatResult, DeepSeekClient, DeepSeekError
from companion.config.settings import DeepSeekSettings, Settings
from companion.core.events import InboundMessage
from companion.core.pipeline import FixedReplyResponder, Responder

logger = logging.getLogger(__name__)


class DeepSeekResponder:
    def __init__(self, client: DeepSeekClient, settings: DeepSeekSettings):
        self._client = client
        self._settings = settings
        self._history: dict[int, deque[dict[str, str]]] = {}

    async def respond(self, message: InboundMessage) -> str:
        limit = self._settings.max_history_messages
        history = self._history.get(message.user_id)
        if history is None:
            history = deque(maxlen=limit)
            self._history[message.user_id] = history
        request_messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._settings.system_prompt}
        ]
        request_messages.extend(history)
        request_messages.append({"role": "user", "content": message.text})
        try:
            result = await self._client.chat(request_messages)
        except DeepSeekError as exc:
            logger.error(
                "DeepSeek 调用失败: kind=%s status=%s user_id=%s detail=%s",
                exc.kind,
                exc.status_code,
                message.user_id,
                exc.detail,
            )
            return exc.user_message
        self._record_usage(message.user_id, result)
        history.append({"role": "user", "content": message.text})
        history.append({"role": "assistant", "content": result.content})
        logger.debug("DeepSeek 回复: user_id=%s content=%s", message.user_id, result.content)
        return result.content

    def _record_usage(self, user_id: int, result: ChatResult) -> None:
        logger.info(
            "DeepSeek 用量: user_id=%s model=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            user_id,
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        )

    async def close(self) -> None:
        await self._client.close()


def build_responder(settings: Settings) -> Responder:
    """按配置构造应答器；deepseek 模式要求 DEEPSEEK_API_KEY 已设置。"""
    if settings.bot.reply_mode != "deepseek":
        return FixedReplyResponder(settings.bot.fixed_reply_text)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("reply_mode 为 deepseek 但 DEEPSEEK_API_KEY 未设置")
    client = DeepSeekClient(settings.deepseek, api_key)
    return DeepSeekResponder(client, settings.deepseek)

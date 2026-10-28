"""DeepSeek chat completions HTTP 适配器（OpenAI 兼容格式）。

只负责协议与传输：URL、鉴权头、超时、状态码到错误类别的映射、usage
解析。上下文组装与重试策略不在这里。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from companion.config.settings import DeepSeekSettings

logger = logging.getLogger(__name__)

ErrorKind = Literal["auth", "balance", "rate_limit", "timeout", "network", "server", "unknown"]

_USER_MESSAGES: dict[str, str] = {
    "auth": "DeepSeek 鉴权失败：请检查 .env 中的 DEEPSEEK_API_KEY 是否正确。",
    "balance": "DeepSeek 账户余额不足，请到 platform.deepseek.com 充值后再试。",
    "rate_limit": "请求过于频繁，请稍后再试。",
    "timeout": "模型响应超时，请稍后再试。",
    "network": "无法连接 DeepSeek 服务，请稍后再试。",
    "server": "DeepSeek 服务暂时不可用，请稍后再试。",
}

_STATUS_KINDS: dict[int, ErrorKind] = {401: "auth", 402: "balance", 429: "rate_limit"}


class DeepSeekError(Exception):
    """DeepSeek 调用失败；kind 用于日志，user_message 直接发给用户。"""

    def __init__(self, kind: ErrorKind, detail: str, status_code: int | None = None):
        super().__init__(f"deepseek {kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status_code = status_code
        self.user_message = _USER_MESSAGES.get(kind, "模型调用失败，请稍后再试。")


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class DeepSeekClient:
    def __init__(
        self,
        settings: DeepSeekSettings,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def chat(self, messages: list[dict[str, Any]]) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "thinking": {"type": "enabled" if self._settings.enable_thinking else "disabled"},
        }
        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekError("timeout", f"{type(exc).__name__}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise DeepSeekError("network", f"{type(exc).__name__}: {exc}") from exc

        if response.status_code != 200:
            kind: ErrorKind = _STATUS_KINDS.get(
                response.status_code,
                "server" if response.status_code >= 500 else "unknown",
            )
            raise DeepSeekError(
                kind, f"HTTP {response.status_code}: {response.text[:300]}", response.status_code
            )

        try:
            data = response.json()
            usage = data.get("usage") or {}
            return ChatResult(
                content=data["choices"][0]["message"]["content"] or "",
                model=str(data.get("model", self._settings.model)),
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekError("unknown", f"响应格式异常: {exc}") from exc

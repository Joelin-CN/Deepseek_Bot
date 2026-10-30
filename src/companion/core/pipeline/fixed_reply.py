"""M1 应答器：始终回复配置的固定文本。"""

from __future__ import annotations

from typing import Protocol

from companion.core.events import InboundMessage
from companion.core.permissions import RunContext


class Responder(Protocol):
    """回复策略：给定入站消息与其执行上下文，产出回复文本。"""

    async def respond(self, message: InboundMessage, ctx: RunContext) -> str: ...


class FixedReplyResponder:
    def __init__(self, text: str):
        self._text = text

    async def respond(self, message: InboundMessage, ctx: RunContext) -> str:
        return self._text

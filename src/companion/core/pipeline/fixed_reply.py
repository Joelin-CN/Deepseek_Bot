"""M1 应答器：始终回复配置的固定文本。"""

from __future__ import annotations

from typing import Protocol

from companion.core.events import InboundMessage


class Responder(Protocol):
    """回复策略：给定入站消息，产出回复文本。"""

    async def respond(self, message: InboundMessage) -> str: ...


class FixedReplyResponder:
    def __init__(self, text: str):
        self._text = text

    async def respond(self, message: InboundMessage) -> str:
        return self._text

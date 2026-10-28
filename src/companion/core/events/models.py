"""Internal message model.

The kernel works only with these shapes; OneBot raw payloads stay inside the
``companion.adapters.onebot`` package and never leak into core modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """解析后的入站私聊消息（筛选通过前的内部表示）。"""

    message_id: int
    user_id: int
    self_id: int
    sub_type: str
    text: str
    time: int

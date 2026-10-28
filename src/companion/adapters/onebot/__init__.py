"""OneBot v11 adapter: forward-WebSocket client for SnowLuma."""

from companion.adapters.onebot.client import (
    ActionError,
    ActionFailed,
    ActionNotSent,
    ActionUnknown,
    OneBotClient,
)
from companion.adapters.onebot.segments import text_segment

__all__ = [
    "ActionError",
    "ActionFailed",
    "ActionNotSent",
    "ActionUnknown",
    "OneBotClient",
    "text_segment",
]

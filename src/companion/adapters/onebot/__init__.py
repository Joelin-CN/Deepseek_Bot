"""OneBot v11 adapter: forward-WebSocket client for SnowLuma."""

from companion.adapters.onebot.client import ActionError, OneBotClient
from companion.adapters.onebot.segments import text_segment

__all__ = ["ActionError", "OneBotClient", "text_segment"]

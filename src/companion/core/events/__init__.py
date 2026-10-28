"""Internal event and message models."""

from companion.core.events.models import InboundMessage
from companion.core.sessions.identity import SessionKey

__all__ = ["InboundMessage", "SessionKey"]

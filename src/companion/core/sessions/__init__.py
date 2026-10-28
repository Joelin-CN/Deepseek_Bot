"""Session identity, dedup and per-conversation execution ordering."""

from companion.core.sessions.identity import SessionKey
from companion.core.sessions.manager import DedupCache, SessionLocks, SessionManager

__all__ = ["DedupCache", "SessionKey", "SessionLocks", "SessionManager"]

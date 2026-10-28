"""Session identity, dedup and per-conversation execution ordering."""

from companion.core.sessions.manager import DedupCache, SessionLocks

__all__ = ["DedupCache", "SessionLocks"]

"""Memory service: scopes, revisions and retrieval.

区分原始消息（messages 表）、短期上下文（内存）、长期记忆（本模块）、
插件 KV 与浏览经历（后续里程碑）。可见性由本服务在查询与上下文组装
前强制：所有操作只作用于当前会话的 scope，跨范围访问在结构上不可达。
"""

from companion.memory.service import MemoryService
from companion.memory.store import MemoryRecord, MemoryStore

__all__ = ["MemoryRecord", "MemoryService", "MemoryStore"]

"""Persistence: database connections, transactions and migrations.

通用连接、事务与迁移在这里；业务规则（会话记录、消息状态、记忆）
在上层服务中，不把 SQL 细节散落到 core 模块。
"""

from companion.persistence.database import Database

__all__ = ["Database"]

"""V2：长期记忆。

status 取值：active（有效）/ superseded（被修订版本替代）/ deleted（软删除）。
修订产生新行并通过 revision_of 链接到旧行；检索只返回 active。
scope 为 SessionKey.as_scope()，可见性由记忆服务在查询前强制。
"""

from companion.persistence.migrations.base import Migration

V2 = Migration(
    version=2,
    name="memories",
    statements=(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source_message_id INTEGER,
            revision_of INTEGER REFERENCES memories (id),
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        )
        """,
        "CREATE INDEX idx_memories_scope_status ON memories (scope, status, id)",
    ),
)

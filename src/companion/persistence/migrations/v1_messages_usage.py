"""V1：会话消息记录与模型用量。

messages 记录一条入站消息从接达到送达的完整状态链：
received → model_generated → sent_ok / send_failed / send_unknown，
delivery_message_id 为平台回执的消息 id（send_unknown 时可能已实际送达，
状态含义见 docs/memory.md 与运行手册）。
"""

from companion.persistence.migrations.base import Migration

V1 = Migration(
    version=1,
    name="messages_and_usage",
    statements=(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            platform TEXT NOT NULL,
            bot_id INTEGER NOT NULL,
            chat_type TEXT NOT NULL,
            chat_target INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            platform_message_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            content_preview TEXT NOT NULL,
            content_len INTEGER NOT NULL,
            reply_preview TEXT NOT NULL DEFAULT '',
            delivery_message_id INTEGER,
            updated_ts REAL NOT NULL
        )
        """,
        "CREATE INDEX idx_messages_session ON messages (session_key, id)",
        "CREATE INDEX idx_messages_status ON messages (status, id)",
        """
        CREATE TABLE usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            session_key TEXT NOT NULL,
            actor_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL
        )
        """,
        "CREATE INDEX idx_usage_ts ON usage (ts)",
    ),
)

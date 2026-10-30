"""V3：消息全文列，用于按会话恢复有界对话历史。

M3.1 起 messages 保存入站与已送达回复的全文（本地私有数据；日志仍
只用预览）。只有 status='sent_ok' 且带回复全文的记录会作为对话轮次
被恢复进短期上下文——发送失败、结果未知或命令回执不构成对话历史。
"""

from companion.persistence.migrations.base import Migration

V3 = Migration(
    version=3,
    name="messages_full_content",
    statements=(
        "ALTER TABLE messages ADD COLUMN content TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE messages ADD COLUMN reply_content TEXT NOT NULL DEFAULT ''",
    ),
)

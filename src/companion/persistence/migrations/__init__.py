"""数据库迁移注册表。按版本升序应用；新迁移只追加，不修改历史项。"""

from companion.persistence.migrations.base import Migration
from companion.persistence.migrations.v1_messages_usage import V1
from companion.persistence.migrations.v2_memories import V2
from companion.persistence.migrations.v3_messages_full_content import V3
from companion.persistence.migrations.v4_plugin_tables import V4

MIGRATIONS: tuple[Migration, ...] = (V1, V2, V3, V4)

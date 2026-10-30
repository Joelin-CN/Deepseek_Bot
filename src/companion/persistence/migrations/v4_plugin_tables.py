"""V4：插件系统表。

- ``plugin_kv``：插件键值存储。namespace 由宿主绑定为插件 ID（主键的
  一部分），value 为 JSON 文本；业务限制（键长、值大小、类型）在
  ``companion.plugin_system.kv`` 层校验，数据库层不重复。
- ``plugin_runtime_state``：运行时启停持久化。启动时若存在本表行则优
  先于配置文件中的 ``plugins.instances.<id>.enabled``。
"""

from companion.persistence.migrations.base import Migration

V4 = Migration(
    version=4,
    name="plugin_tables",
    statements=(
        """CREATE TABLE plugin_kv (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_ts REAL NOT NULL,
            PRIMARY KEY (namespace, key)
        )""",
        """CREATE TABLE plugin_runtime_state (
            plugin_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            updated_ts REAL NOT NULL
        )""",
    ),
)

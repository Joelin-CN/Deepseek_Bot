"""插件 KV 存储（plugin_kv 表）与运行状态持久化（plugin_runtime_state 表）。

KV 是插件自己的状态存储，与记忆服务（memories 表）互不相干：宿主只提
供本模块的通用键值读写，不做任何记忆语义解释。
"""

from __future__ import annotations

import json
import time
from typing import Any

from companion.api import (
    KVKeyError,
    KVTypeError,
    KVValueTooLarge,
    PluginKV,
    valid_plugin_id,
)
from companion.persistence import Database

MAX_KEY_LEN = 128
MAX_VALUE_BYTES = 64 * 1024  # JSON 序列化后的 UTF-8 字节数上限

_KEY_RE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-/"
)


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_LEN:
        raise KVKeyError(f"键必须是非空字符串且不超过 {MAX_KEY_LEN} 个字符")
    if any(ch not in _KEY_RE_CHARS for ch in key):
        raise KVKeyError("键只允许字母、数字和 _ . : - 字符")
    return key


class _BoundKV(PluginKV):
    """绑定单个命名空间的插件视图；插件代码拿不到 namespace 参数。"""

    def __init__(self, store: "PluginKVStore", namespace: str):
        self._store = store
        self._namespace = namespace

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self._store._db.fetchone(
            "SELECT value FROM plugin_kv WHERE namespace = ? AND key = ?",
            (self._namespace, _check_key(key)),
        )
        if row is None:
            return default
        return json.loads(row[0])

    async def set(self, key: str, value: Any) -> None:
        encoded = _encode_value(value)
        await self._store._db.execute(
            "INSERT INTO plugin_kv (namespace, key, value, updated_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, "
            "updated_ts = excluded.updated_ts",
            (self._namespace, _check_key(key), encoded, time.time()),
        )

    async def delete(self, key: str) -> bool:
        cursor_row = await self._store._db.fetchone(
            "SELECT 1 FROM plugin_kv WHERE namespace = ? AND key = ?",
            (self._namespace, _check_key(key)),
        )
        if cursor_row is None:
            return False
        await self._store._db.execute(
            "DELETE FROM plugin_kv WHERE namespace = ? AND key = ?",
            (self._namespace, key),
        )
        return True

    async def keys(self) -> list[str]:
        rows = await self._store._db.fetchall(
            "SELECT key FROM plugin_kv WHERE namespace = ? ORDER BY key",
            (self._namespace,),
        )
        return [row[0] for row in rows]

    async def count(self) -> int:
        row = await self._store._db.fetchone(
            "SELECT COUNT(*) FROM plugin_kv WHERE namespace = ?", (self._namespace,)
        )
        return int(row[0]) if row else 0


def _encode_value(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise KVTypeError(f"值无法 JSON 序列化：{exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise KVValueTooLarge(
            f"值序列化后超过 {MAX_VALUE_BYTES} 字节上限"
        )
    return encoded


class PluginKVStore:
    """通用 KV 存取；命名空间由宿主绑定后以视图形式发放给插件。"""

    def __init__(self, db: Database):
        self._db = db

    def bind(self, namespace: str) -> PluginKV:
        """把命名空间绑定为插件视图。命名空间必须是合法插件 ID。"""
        if not valid_plugin_id(namespace):
            raise KVKeyError(f"命名空间非法：{namespace!r}")
        return _BoundKV(self, namespace)


class PluginRuntimeStateStore:
    """运行时启停状态（plugin_runtime_state 表）。

    规则：启动时若存在本表行，优先于配置文件的 enabled；显式管理操作
    （/plugin enable|disable）写入本表。进程正常退出不回写本表。
    """

    def __init__(self, db: Database):
        self._db = db

    async def get(self, plugin_id: str) -> bool | None:
        row = await self._db.fetchone(
            "SELECT enabled FROM plugin_runtime_state WHERE plugin_id = ?",
            (plugin_id,),
        )
        if row is None:
            return None
        return bool(row[0])

    async def set(self, plugin_id: str, enabled: bool) -> None:
        await self._db.execute(
            "INSERT INTO plugin_runtime_state (plugin_id, enabled, updated_ts) "
            "VALUES (?, ?, ?) ON CONFLICT(plugin_id) DO UPDATE SET "
            "enabled = excluded.enabled, updated_ts = excluded.updated_ts",
            (plugin_id, 1 if enabled else 0, time.time()),
        )

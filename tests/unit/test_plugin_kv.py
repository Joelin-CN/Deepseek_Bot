"""插件 KV：类型、限额、命名空间隔离与重启持久化。"""

from __future__ import annotations

import pytest

from companion.api import KVKeyError, KVTypeError, KVValueTooLarge
from companion.persistence import Database
from companion.plugin_system.kv import PluginKVStore

NAMESPACE_A = "t.alpha"
NAMESPACE_B = "t.beta"


async def make_store(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    return PluginKVStore(db), db


async def test_roundtrip_json_types(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        values = {
            "i": 42, "f": 3.14, "s": "文本", "b": True, "n": None,
            "list": [1, "a", None], "dict": {"k": ["v", 2]},
        }
        for key, value in values.items():
            await kv.set(key, value)
        for key, value in values.items():
            assert await kv.get(key) == value
        assert await kv.get("missing", "默认") == "默认"
    finally:
        await db.close()


async def test_overwrite_and_delete(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        await kv.set("k", 1)
        await kv.set("k", 2)
        assert await kv.get("k") == 2
        assert await kv.delete("k") is True
        assert await kv.delete("k") is False
        assert await kv.get("k", 0) == 0
    finally:
        await db.close()


async def test_keys_sorted_and_count(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        for key in ("b", "a", "c"):
            await kv.set(key, 1)
        assert await kv.keys() == ["a", "b", "c"]
        assert await kv.count() == 3
    finally:
        await db.close()


async def test_namespace_isolation(tmp_path):
    store, db = await make_store(tmp_path)
    kv_a = store.bind(NAMESPACE_A)
    kv_b = store.bind(NAMESPACE_B)
    try:
        await kv_a.set("shared", "来自A")
        await kv_b.set("shared", "来自B")
        assert await kv_a.get("shared") == "来自A"
        assert await kv_b.get("shared") == "来自B"
        assert await kv_a.count() == 1 and await kv_b.count() == 1
        await kv_a.delete("shared")
        assert await kv_b.get("shared") == "来自B"
    finally:
        await db.close()


async def test_persistence_across_reopen(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    await kv.set("keep", {"n": 7})
    await db.close()

    db2 = await Database.open(tmp_path / "bot.db")
    kv2 = PluginKVStore(db2).bind(NAMESPACE_A)
    try:
        assert await kv2.get("keep") == {"n": 7}
        assert await kv2.keys() == ["keep"]
    finally:
        await db2.close()


@pytest.mark.parametrize("bad", ["", "x" * 129, "bad key!", "斜杠/通配%", "a\nb"])
async def test_invalid_keys_rejected(tmp_path, bad):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        with pytest.raises(KVKeyError):
            await kv.set(bad, 1)
    finally:
        await db.close()


async def test_key_with_colon_and_scope_chars_ok(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        await kv.set("count:qq/1/private/2", 5)
        assert await kv.get("count:qq/1/private/2") == 5
    finally:
        await db.close()


async def test_value_too_large_rejected(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        with pytest.raises(KVValueTooLarge):
            await kv.set("big", "x" * (64 * 1024 + 100))
    finally:
        await db.close()


async def test_non_serializable_value_rejected(tmp_path):
    store, db = await make_store(tmp_path)
    kv = store.bind(NAMESPACE_A)
    try:
        with pytest.raises(KVTypeError):
            await kv.set("bad", {1, 2, 3})
        with pytest.raises(KVTypeError):
            await kv.set("bad2", object())
    finally:
        await db.close()


async def test_bind_invalid_namespace_rejected(tmp_path):
    store, db = await make_store(tmp_path)
    try:
        with pytest.raises(KVKeyError):
            store.bind("../escape")
        with pytest.raises(KVKeyError):
            store.bind("")
    finally:
        await db.close()

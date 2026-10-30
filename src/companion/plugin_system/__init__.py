"""插件系统：宿主、清单、加载器、注册表与 KV/状态存储。

分层：``companion.api`` 定义公共接口；本包实现加载与生命周期，内核
（core）不依赖本包内部结构，宿主经 ``PluginHost`` 被 runner 接线。
"""

from companion.plugin_system.host import PluginHost, PluginState, PluginStatus
from companion.plugin_system.kv import PluginKVStore, PluginRuntimeStateStore

__all__ = [
    "PluginHost",
    "PluginState",
    "PluginStatus",
    "PluginKVStore",
    "PluginRuntimeStateStore",
]

"""插件元信息与原生 API 版本。

框架版本（companion.__version__）与插件 API 版本是两回事：框架每个里
程碑都可能变化，而插件 API 只在公共接口语义变化时升版。插件在清单中
声明它要求的 api_version，宿主在导入插件代码之前完成兼容判定。

版本规则：``主版本.次版本``。主版本不同即不兼容；主版本相同则插件要
求的次版本必须不高于当前实现（宿主向后兼容旧次版本的插件）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 当前原生插件 API 版本（与 companion.__version__ 框架版本相互独立）
#: 1.1（M5）：register_tool 新增 required_capability / wants_context；v2 工具
#: 处理器接收 ToolExecutionContext；新增 Capability.WEB_BROWSE。
PLUGIN_API_VERSION = "1.1"

_API_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")

#: 插件 ID 长度上限（同时用于数据目录名与 KV 命名空间，必须防路径穿越）
MAX_PLUGIN_ID_LEN = 64


def parse_api_version(value: str) -> tuple[int, int]:
    """解析 ``主.次`` 版本字符串，非法时抛 ValueError。"""
    match = _API_VERSION_RE.match(value.strip())
    if match is None:
        raise ValueError(f"API 版本必须是 '主.次' 形式（如 1.0），收到：{value!r}")
    return int(match.group(1)), int(match.group(2))


def api_version_compatible(required: str, current: str = PLUGIN_API_VERSION) -> bool:
    """判定插件要求的 API 版本能否由当前宿主满足。"""
    req_major, req_minor = parse_api_version(required)
    cur_major, cur_minor = parse_api_version(current)
    return req_major == cur_major and req_minor <= cur_minor


def valid_plugin_id(plugin_id: str) -> bool:
    """插件 ID 合法性：小写段式标识（如 ``builtin.hello``），段内字母数字下划线。"""
    return bool(plugin_id) and len(plugin_id) <= MAX_PLUGIN_ID_LEN and bool(
        PLUGIN_ID_RE.match(plugin_id)
    )


def valid_version(version: str) -> bool:
    return bool(version) and len(version) <= 32 and bool(_VERSION_RE.match(version))


@dataclass(frozen=True)
class ConfigFieldSpec:
    """插件配置项定义：类型、缺省值与说明。

    类型限定为 string/int/float/bool/list；缺省值必须与声明类型一致。
    宿主据此校验 ``config/local.json`` 中 ``plugins.instances.<id>.config``
    传入的值，非法值在启用时报告，不会静默采用。
    """

    type: str  # "string" | "int" | "float" | "bool" | "list"
    default: object = None
    description: str = ""

    @classmethod
    def allowed_types(cls) -> frozenset[str]:
        return frozenset({"string", "int", "float", "bool", "list"})


@dataclass(frozen=True)
class PluginMetadata:
    """manifest.json 的校验结果。

    ``entry`` 形如 ``main:ClassName``：模块文件名不含扩展名（必须是插件
    目录内的直接文件，不允许路径分隔符），类名必须是该模块导出的
    ``companion.api.Plugin`` 子类。
    """

    plugin_id: str
    name: str
    version: str
    api_version: str
    entry: str
    description: str = ""
    config_schema: dict[str, ConfigFieldSpec] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    directory: Path | None = None  # 由加载器填充，便于日志与数据目录定位

    @property
    def entry_module(self) -> str:
        module, _, _ = self.entry.partition(":")
        return module

    @property
    def entry_class(self) -> str:
        _, _, cls = self.entry.partition(":")
        return cls

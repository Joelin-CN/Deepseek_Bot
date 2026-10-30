"""manifest.json 解析与校验。

清单是插件身份的唯一来源：宿主在**导入插件代码之前**完成全部校验
（ID、版本、入口、API 版本、依赖声明、配置定义），重复 ID 与非法值在
这一步就被拒绝。这样加载失败永远不会执行到不可信入口代码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from companion.api import (
    PLUGIN_API_VERSION,
    ConfigFieldSpec,
    PluginAPIVersionError,
    PluginError,
    PluginMetadata,
    api_version_compatible,
    parse_api_version,
    valid_plugin_id,
    valid_version,
)

MANIFEST_FILENAME = "manifest.json"
MAX_NAME_LEN = 128
MAX_DESCRIPTION_LEN = 1024
MAX_CONFIG_FIELDS = 64
MAX_CONFIG_KEY_LEN = 64

_CONFIG_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ManifestError(PluginError):
    """清单缺失、不是合法 JSON 或字段校验失败。"""


def load_manifest(directory: Path) -> PluginMetadata:
    """从插件目录读取并校验 manifest.json。任何问题抛 ManifestError。"""
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(f"缺少 {MANIFEST_FILENAME}：{directory}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} 顶层必须是 JSON 对象")

    plugin_id = _require_str(raw, "id", path)
    if not valid_plugin_id(plugin_id):
        raise ManifestError(
            f"{path} 的 id 非法：{plugin_id!r}。要求小写段式标识（如 builtin.hello），"
            f"段内限字母/数字/下划线，总长不超过 64"
        )
    name = _require_str(raw, "name", path)
    if len(name) > MAX_NAME_LEN:
        raise ManifestError(f"{path} 的 name 超过 {MAX_NAME_LEN} 字符")
    version = _require_str(raw, "version", path)
    if not valid_version(version):
        raise ManifestError(f"{path} 的 version 非法：{version!r}（要求如 1.0.0）")
    api_version = _require_str(raw, "api_version", path)
    try:
        parse_api_version(api_version)
    except ValueError as exc:
        raise ManifestError(f"{path} 的 api_version 非法：{exc}") from exc
    entry = _require_str(raw, "entry", path)
    _validate_entry(entry, path)

    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LEN:
        raise ManifestError(f"{path} 的 description 必须是不超过 {MAX_DESCRIPTION_LEN} 字符的字符串")

    config_schema = _load_config_schema(raw.get("config", {}), path)

    requires_raw = raw.get("requires", [])
    if not isinstance(requires_raw, list) or any(
        not isinstance(item, str) or not item.strip() for item in requires_raw
    ):
        raise ManifestError(f"{path} 的 requires 必须是依赖包名字符串列表")
    requires = tuple(dict.fromkeys(item.strip() for item in requires_raw))

    if not api_version_compatible(api_version):
        raise PluginAPIVersionError(
            f"插件 {plugin_id} 要求原生 API 版本 {api_version}，"
            f"当前宿主提供的是 {PLUGIN_API_VERSION}，不兼容"
        )

    return PluginMetadata(
        plugin_id=plugin_id,
        name=name,
        version=version,
        api_version=api_version,
        entry=entry,
        description=description,
        config_schema=config_schema,
        requires=requires,
        directory=directory,
    )


def _require_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path} 缺少必填字符串字段 {key}")
    return value.strip()


def _validate_entry(entry: str, path: Path) -> None:
    module, sep, cls = entry.partition(":")
    if not sep or not module or not cls:
        raise ManifestError(
            f"{path} 的 entry 必须形如 'main:ClassName'，收到：{entry!r}"
        )
    if not _MODULE_RE.match(module):
        raise ManifestError(
            f"{path} 的 entry 模块名非法：{module!r}（须为插件目录内的 .py 文件名，不含路径分隔符）"
        )
    if not _CLASS_RE.match(cls):
        raise ManifestError(f"{path} 的 entry 类名非法：{cls!r}")


def _load_config_schema(raw: Any, path: Path) -> dict[str, ConfigFieldSpec]:
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} 的 config 必须是对象")
    if len(raw) > MAX_CONFIG_FIELDS:
        raise ManifestError(f"{path} 的 config 项数超过 {MAX_CONFIG_FIELDS}")
    schema: dict[str, ConfigFieldSpec] = {}
    for key, spec_raw in raw.items():
        if not isinstance(key, str) or not key or len(key) > MAX_CONFIG_KEY_LEN or not _CONFIG_KEY_RE.match(key):
            raise ManifestError(f"{path} 的配置键非法：{key!r}")
        if not isinstance(spec_raw, dict):
            raise ManifestError(f"{path} 的配置项 {key} 必须是对象")
        field_type = spec_raw.get("type")
        if field_type not in ConfigFieldSpec.allowed_types():
            raise ManifestError(
                f"{path} 的配置项 {key} 的 type 必须是 "
                f"{'/'.join(sorted(ConfigFieldSpec.allowed_types()))}，收到 {field_type!r}"
            )
        default = spec_raw.get("default")
        _check_default_matches(key, field_type, default, path)
        description = spec_raw.get("description", "")
        if not isinstance(description, str) or len(description) > 256:
            raise ManifestError(f"{path} 的配置项 {key} 的 description 非法")
        schema[key] = ConfigFieldSpec(
            type=field_type, default=default, description=description
        )
    return schema


def _check_default_matches(key: str, field_type: str, default: Any, path: Path) -> None:
    ok: bool
    if field_type == "string":
        ok = isinstance(default, str)
    elif field_type == "int":
        ok = isinstance(default, int) and not isinstance(default, bool)
    elif field_type == "float":
        ok = isinstance(default, (int, float)) and not isinstance(default, bool)
    elif field_type == "bool":
        ok = isinstance(default, bool)
    else:  # list
        ok = isinstance(default, list)
    if not ok:
        raise ManifestError(
            f"{path} 的配置项 {key} 声明 type={field_type}，但缺省值 {default!r} 类型不符"
        )

"""插件发现与加载。

发现：扫描各插件根目录下含 ``manifest.json`` 的一级子目录。
加载：校验清单 → 检查依赖可导入 → 校验 API 版本兼容 → 按文件路径导入
入口模块 → 取出 ``companion.api.Plugin`` 子类并实例化。

以 ``importlib`` 按文件路径加载，不向 ``sys.path`` 注入插件目录；插件
模块注册在合成名 ``companion_plugin_<id>__<module>`` 下。重复加载同一
插件会重新执行模块并覆盖同名条目——停用/卸载只撤销注册与释放资源，
**不做 Python 模块级卸载，也不支持热更新**。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from companion.api import Plugin, PluginAPIVersionError, PluginLoadError, PluginMetadata
from companion.plugin_system.manifest import MANIFEST_FILENAME, ManifestError, load_manifest

logger = logging.getLogger(__name__)


@dataclass
class LoadedPlugin:
    metadata: PluginMetadata
    instance: Plugin
    module: types.ModuleType


def discover_plugins(roots: list[Path]) -> list[Path]:
    """返回所有根目录下含 manifest.json 的一级子目录（保持根顺序）。"""
    seen: set[Path] = set()
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            logger.debug("插件根目录不存在，跳过: %s", root)
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            resolved = child.resolve()
            if resolved in seen:
                continue
            if (child / MANIFEST_FILENAME).is_file():
                seen.add(resolved)
                found.append(child)
    return found


def check_dependencies(metadata: PluginMetadata) -> None:
    """校验 manifest.requires 声明的依赖在本环境中可导入。

    只做可导入检查，不自动安装、不升级环境；缺失时报告全部缺失项。
    """
    missing = []
    for package in metadata.requires:
        try:
            if importlib.util.find_spec(package) is None:
                missing.append(package)
        except (ImportError, ValueError):
            missing.append(package)
    if missing:
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 声明的依赖未安装：{', '.join(missing)}。"
            "宿主不会自动安装插件依赖；请在该插件文档确认后手动安装到 Conda 环境。"
        )


def load_plugin(directory: Path) -> LoadedPlugin:
    """加载单个插件目录；任何失败抛 PluginError 子类。"""
    try:
        metadata = load_manifest(directory)
    except PluginAPIVersionError:
        raise
    except ManifestError as exc:
        raise PluginLoadError(str(exc)) from exc
    check_dependencies(metadata)

    module_name = f"companion_plugin_{metadata.plugin_id.replace('.', '_')}__{metadata.entry_module}"
    entry_path = directory / f"{metadata.entry_module}.py"
    if not entry_path.is_file():
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 的入口文件不存在：{entry_path}"
        )
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"无法为 {entry_path} 构建导入 spec")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 的入口模块执行失败：{type(exc).__name__}: {exc}"
        ) from exc
    sys.modules[module_name] = module

    entry_class = getattr(module, metadata.entry_class, None)
    if entry_class is None:
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 的模块 {metadata.entry_module}.py "
            f"没有导出 {metadata.entry_class}"
        )
    if not (isinstance(entry_class, type) and issubclass(entry_class, Plugin)):
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 的入口类 {metadata.entry_class} "
            "必须继承 companion.api.Plugin"
        )
    try:
        instance = entry_class()
    except Exception as exc:
        raise PluginLoadError(
            f"插件 {metadata.plugin_id} 实例化失败：{type(exc).__name__}: {exc}"
        ) from exc
    logger.info(
        "插件已加载: %s %s（api %s）from %s",
        metadata.plugin_id, metadata.version, metadata.api_version, directory,
    )
    return LoadedPlugin(metadata=metadata, instance=instance, module=module)

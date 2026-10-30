"""插件发现与加载：入口错误、依赖缺失、API 不兼容各有明确报告。"""

from __future__ import annotations

import pytest

from companion.api import Plugin, PluginAPIVersionError, PluginLoadError
from companion.plugin_system.loader import discover_plugins, load_plugin

from plugin_harness import make_plugin


async def test_discover_finds_manifest_dirs_only(tmp_path):
    root = tmp_path / "plugins"
    make_plugin(root, "a.one")
    make_plugin(root, "b.two")
    (root / "not_a_plugin").mkdir(parents=True)  # 无 manifest
    (root / ".hidden_dir").mkdir(parents=True)
    found = discover_plugins([root])
    assert [d.name for d in found] == ["a_one", "b_two"]


async def test_discover_skips_missing_root_and_dedupes(tmp_path):
    found = discover_plugins([tmp_path / "missing", tmp_path / "plugins", tmp_path / "plugins"])
    assert found == []
    root = tmp_path / "plugins"
    make_plugin(root, "a.one")
    assert len(discover_plugins([root, root])) == 1


async def test_load_ok_returns_plugin_instance(tmp_path):
    directory = make_plugin(tmp_path, "a.ok")
    loaded = load_plugin(directory)
    assert loaded.metadata.plugin_id == "a.ok"
    assert isinstance(loaded.instance, Plugin)


async def test_load_missing_entry_file(tmp_path):
    # 清单 entry 指向 main.py，但目录里实际写的是 other.py
    directory = make_plugin(
        tmp_path, "a.file", module="other",
        manifest_overrides={"entry": "main:TestPlugin"},
    )
    with pytest.raises(PluginLoadError, match="入口文件不存在"):
        load_plugin(directory)


async def test_load_entry_class_missing(tmp_path):
    directory = make_plugin(tmp_path, "a.cls", entry_class="Nope")
    with pytest.raises(PluginLoadError, match="没有导出 Nope"):
        load_plugin(directory)


async def test_load_entry_not_plugin_subclass(tmp_path):
    directory = tmp_path / "a_notpl"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        '{"id": "a.notpl", "name": "x", "version": "1.0.0", "api_version": "1.0", "entry": "main:NotPlugin"}',
        encoding="utf-8",
    )
    (directory / "main.py").write_text(
        "class NotPlugin:\n    pass\n", encoding="utf-8"
    )
    with pytest.raises(PluginLoadError, match="必须继承 companion.api.Plugin"):
        load_plugin(directory)


async def test_load_module_raises(tmp_path):
    directory = tmp_path / "a_raise"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        '{"id": "a.raise", "name": "x", "version": "1.0.0", "api_version": "1.0", "entry": "main:TestPlugin"}',
        encoding="utf-8",
    )
    (directory / "main.py").write_text("raise RuntimeError('坏插件')\n", encoding="utf-8")
    with pytest.raises(PluginLoadError, match="入口模块执行失败.*坏插件"):
        load_plugin(directory)


async def test_load_missing_dependency_reported(tmp_path):
    directory = make_plugin(
        tmp_path, "a.dep",
        manifest_overrides={"requires": ["definitely_missing_pkg_xyz", "also_missing_abc"]},
    )
    with pytest.raises(PluginLoadError, match="definitely_missing_pkg_xyz.*also_missing_abc"):
        load_plugin(directory)


async def test_load_api_version_incompatible(tmp_path):
    directory = make_plugin(tmp_path, "a.v2", manifest_overrides={"api_version": "2.0"})
    with pytest.raises(PluginAPIVersionError):
        load_plugin(directory)

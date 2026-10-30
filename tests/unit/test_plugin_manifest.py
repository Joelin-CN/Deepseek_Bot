"""清单解析与校验：非法元信息在导入插件代码前被拒绝。"""

from __future__ import annotations

import json

import pytest

from companion.api import PluginAPIVersionError, valid_plugin_id, valid_version
from companion.plugin_system.manifest import ManifestError, load_manifest

from plugin_harness import make_plugin


def _valid(root):
    return make_plugin(root, "t.good", manifest_overrides={
        "config": {"greeting": {"type": "string", "default": "你好", "description": "问候"}},
        "requires": [],
    })


async def test_valid_manifest_parsed(tmp_path):
    meta = load_manifest(_valid(tmp_path))
    assert meta.plugin_id == "t.good"
    assert meta.name == "插件t.good"
    assert meta.version == "1.0.0"
    assert meta.api_version == "1.0"
    assert meta.entry == "main:TestPlugin"
    assert meta.entry_module == "main" and meta.entry_class == "TestPlugin"
    assert meta.config_schema["greeting"].default == "你好"
    assert meta.requires == ()
    assert meta.directory is not None


async def test_missing_manifest_file(tmp_path):
    with pytest.raises(ManifestError, match="缺少 manifest.json"):
        load_manifest(tmp_path / "not_exist")


async def test_manifest_not_json(tmp_path):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "manifest.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(ManifestError, match="不是合法 JSON"):
        load_manifest(directory)


async def test_manifest_top_level_not_object(tmp_path):
    directory = tmp_path / "arr"
    directory.mkdir()
    (directory / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestError, match="顶层必须是 JSON 对象"):
        load_manifest(directory)


@pytest.mark.parametrize("field", ["id", "name", "version", "api_version", "entry"])
async def test_missing_required_fields(tmp_path, field):
    directory = _valid(tmp_path)
    raw = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    del raw[field]
    (directory / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ManifestError, match=f"缺少必填字符串字段 {field}"):
        load_manifest(directory)


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "C:\\win", "UPPER.x", "1start", "a b", "x" * 65, "-dash", "dot..double"],
)
async def test_invalid_plugin_ids_rejected(tmp_path, bad_id):
    make_plugin(tmp_path, "t.id", manifest_overrides={"id": bad_id})
    with pytest.raises(ManifestError, match="的 id 非法"):
        load_manifest(tmp_path / "t_id")


async def test_invalid_version_rejected(tmp_path):
    make_plugin(tmp_path, "t.ver", manifest_overrides={"version": "v1.0"})
    with pytest.raises(ManifestError, match="version 非法"):
        load_manifest(tmp_path / "t_ver")


async def test_invalid_api_version_format(tmp_path):
    make_plugin(tmp_path, "t.api", manifest_overrides={"api_version": "one"})
    with pytest.raises(ManifestError, match="api_version 非法"):
        load_manifest(tmp_path / "t_api")


@pytest.mark.parametrize("bad", ["2.0", "0.9", "1.99"])
async def test_incompatible_api_version(tmp_path, bad):
    make_plugin(tmp_path, "t.compat", manifest_overrides={"api_version": bad})
    with pytest.raises(PluginAPIVersionError, match="不兼容"):
        load_manifest(tmp_path / "t_compat")


@pytest.mark.parametrize("bad", ["main", "main:", ":Cls", "sub/dir:Cls", "main:bad-class"])
async def test_invalid_entry_forms(tmp_path, bad):
    make_plugin(tmp_path, "t.entry", manifest_overrides={"entry": bad})
    with pytest.raises(ManifestError, match="entry"):
        load_manifest(tmp_path / "t_entry")


async def test_config_type_not_allowed(tmp_path):
    make_plugin(tmp_path, "t.cfg1", manifest_overrides={
        "config": {"k": {"type": "dict", "default": {}}}})
    with pytest.raises(ManifestError, match="type 必须是"):
        load_manifest(tmp_path / "t_cfg1")


async def test_config_default_type_mismatch(tmp_path):
    make_plugin(tmp_path, "t.cfg2", manifest_overrides={
        "config": {"k": {"type": "int", "default": "x"}}})
    with pytest.raises(ManifestError, match="缺省值 .* 类型不符"):
        load_manifest(tmp_path / "t_cfg2")


async def test_config_key_invalid(tmp_path):
    make_plugin(tmp_path, "t.cfg3", manifest_overrides={
        "config": {"bad-key!": {"type": "string", "default": ""}}})
    with pytest.raises(ManifestError, match="配置键非法"):
        load_manifest(tmp_path / "t_cfg3")


async def test_config_field_not_object(tmp_path):
    make_plugin(tmp_path, "t.cfg4", manifest_overrides={"config": {"k": "string"}})
    with pytest.raises(ManifestError, match="配置项 k 必须是对象"):
        load_manifest(tmp_path / "t_cfg4")


async def test_requires_must_be_list_of_names(tmp_path):
    make_plugin(tmp_path, "t.req", manifest_overrides={"requires": "numpy"})
    with pytest.raises(ManifestError, match="requires 必须是依赖包名字符串列表"):
        load_manifest(tmp_path / "t_req")


def test_plugin_id_helpers():
    assert valid_plugin_id("builtin.hello")
    assert valid_plugin_id("a")
    assert not valid_plugin_id("../x")
    assert not valid_plugin_id("")
    assert not valid_plugin_id("Bad")
    assert valid_version("1.0.0") and valid_version("2")
    assert not valid_version("v1") and not valid_version("")

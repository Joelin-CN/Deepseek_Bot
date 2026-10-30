"""工具注册表：登记、冲突、归属与撤销。"""

from __future__ import annotations

import pytest

from companion.core.tools import ToolConflictError, ToolDescriptor, ToolNameError, ToolRegistry


def descriptor(name: str, owner: str = "t.demo") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"工具 {name}",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        owner=owner,
    )


async def test_register_and_query():
    registry = ToolRegistry()

    async def handler(params):
        return "ok"

    registry.register(descriptor("demo_tool"), handler)
    got = registry.get("demo_tool")
    assert got is not None and got.owner == "t.demo"
    assert got.description == "工具 demo_tool"
    assert registry.executor("demo_tool") is handler
    assert len(registry) == 1


async def test_duplicate_name_conflict():
    registry = ToolRegistry()
    registry.register(descriptor("dup"), lambda params: None)
    with pytest.raises(ToolConflictError, match="已由 t.demo 登记"):
        registry.register(descriptor("dup", owner="t.other"), lambda params: None)


@pytest.mark.parametrize("bad", ["", "1abc", "Upper", "has space", "x" * 65, "中文"])
async def test_invalid_names_rejected(bad):
    registry = ToolRegistry()
    with pytest.raises(ToolNameError):
        registry.register(descriptor(bad), lambda params: None)


async def test_unregister_and_unregister_owner():
    registry = ToolRegistry()
    registry.register(descriptor("a"), lambda params: None)
    registry.register(descriptor("b"), lambda params: None)
    registry.register(descriptor("c", owner="t.other"), lambda params: None)

    assert registry.unregister("a") is True
    assert registry.unregister("a") is False
    removed = registry.unregister_owner("t.demo")
    assert removed == ["b"]
    assert [d.name for d in registry.list_tools()] == ["c"]
    assert registry.list_tools(owner="t.other") == [registry.get("c")]
    assert registry.list_tools(owner="t.demo") == []

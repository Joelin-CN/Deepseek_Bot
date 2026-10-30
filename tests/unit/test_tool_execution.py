"""统一工具执行服务：权限、启停复查、参数校验、超时、取消与结果。"""

from __future__ import annotations

import asyncio
import json

import pytest

from companion.core.permissions import Capability, RunContext, actor_for
from companion.core.sessions.identity import SessionKey
from companion.core.tools import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionResult,
    ToolExecutionService,
    ToolRegistry,
    ToolSchemaError,
    build_name_mapping,
    clip_result,
    validate_arguments,
    validate_schema,
)

OWNER = 844281600
MEMBER = 10001
SELF = 3666167977


def ctx_for(user_id: int = OWNER, cancel_set: bool = False) -> RunContext:
    actor = actor_for("qq", user_id, "测试", owner_id=OWNER, allowed_senders={OWNER, MEMBER})
    ctx = RunContext(session=SessionKey("qq", SELF, "private", user_id), actor=actor)
    if cancel_set:
        ctx.cancel.set()
    return ctx


def make_service(active_owner: str | None = "t.owner", **kwargs) -> ToolExecutionService:
    return ToolExecutionService(
        registry=kwargs.pop("registry", ToolRegistry()),
        is_owner_active=(lambda owner: owner == active_owner) if active_owner is not None else None,
        tool_timeout=kwargs.pop("tool_timeout", 2.0),
        **kwargs,
    )


NO_ARGS = {"type": "object", "properties": {}, "required": []}


async def reg_add(registry: ToolRegistry, name="add", *, capability: str | None = None,
                  wants_context=False, handler=None, schema=None, owner="t.owner"):
    async def default_handler(params):
        return {"sum": params["a"] + params["b"]}

    if schema is None:
        schema = NO_ARGS if handler is not None else {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }
    registry.register(
        ToolDescriptor(
            name=name,
            description="加法",
            parameters=schema,
            owner=owner,
            required_capability=capability,
            wants_context=wants_context,
        ),
        handler or default_handler,
    )


# ---- 权限过滤与启停 ----


async def test_list_for_filters_by_capability_and_plugin_state():
    registry = ToolRegistry()
    await reg_add(registry, "plain_add")
    await reg_add(registry, "web_fetch", capability=Capability.WEB_BROWSE.value)
    await reg_add(registry, "dead_tool", owner="t.dead")  # 归属停用插件

    service = make_service(registry=registry, active_owner="t.owner")
    owner_visible = [name for name, _ in service.list_for(ctx_for(OWNER))]
    member_visible = [name for name, _ in service.list_for(ctx_for(MEMBER))]

    assert owner_visible == ["plain_add", "web_fetch"]
    assert member_visible == ["plain_add"]  # 成员看不到主人专属工具

    service_off = make_service(registry=registry, active_owner="__none__")  # 全部插件视为停用
    assert service_off.list_for(ctx_for(OWNER)) == []


async def test_execute_checks_plugin_disabled_after_listing():
    registry = ToolRegistry()
    await reg_add(registry, "add")
    service = make_service(registry=registry, active_owner="t.owner")
    service.list_for(ctx_for(OWNER))  # 建立名称映射
    service.is_owner_active = lambda owner: False  # 列表拿到后插件被停用

    result = await service.execute(ToolCall(id="c1", name="add", arguments='{"a":1,"b":2}'), ctx_for(OWNER), task_id="t1")
    assert result.ok is False and result.error == "plugin_disabled"


async def test_execute_unknown_tool():
    service = make_service()
    result = await service.execute(ToolCall(id="c1", name="nope", arguments="{}"), ctx_for(OWNER), task_id="t1")
    assert result.error == "unknown_tool"


async def test_execute_permission_denied():
    registry = ToolRegistry()
    await reg_add(registry, "web_fetch", capability=Capability.WEB_BROWSE.value)
    service = make_service(registry=registry)
    service.list_for(ctx_for(OWNER))  # 模型名单已建立（主人视角）
    result = await service.execute(ToolCall(id="c1", name="web_fetch", arguments="{}"), ctx_for(MEMBER), task_id="t1")
    assert result.error == "permission_denied"


# ---- 参数校验 ----


@pytest.mark.parametrize(
    "raw, expect_error",
    [
        ("{oops", "参数不是合法 JSON"),
        ("[1,2]", "参数必须是 JSON 对象"),
        ('{"a":1}', "缺少必填参数 b"),
        ('{"a":"x","b":2}', "要求类型 integer"),
        ('{"a":1,"b":2,"c":true}', None),  # 额外键放行
    ],
)
async def test_validate_arguments_cases(raw, expect_error):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}
    params, error = validate_arguments(raw, schema)
    if expect_error is None:
        assert error is None
    else:
        assert error is not None and expect_error in error


async def test_execute_invalid_arguments_returns_structured_failure():
    registry = ToolRegistry()
    await reg_add(registry, "add")
    service = make_service(registry=registry)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="c1", name="add", arguments="{bad"), ctx_for(OWNER), task_id="t1")
    assert result.ok is False and result.error == "invalid_arguments"
    assert "JSON" in result.content["detail"]


async def test_arguments_too_large_rejected():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": []}
    params, error = validate_arguments(json.dumps({"a": 1, "pad": "x" * 40000}), schema)
    assert error is not None and "字节上限" in error


# ---- 执行：签名、超时、取消、异常 ----


async def test_v1_handler_receives_params_only():
    registry = ToolRegistry()
    seen = {}

    async def handler(params):
        seen.update(params)
        return {"ok": True}

    await reg_add(registry, "probe", handler=handler,
                 schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]})
    service = make_service(registry=registry)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="c1", name="probe", arguments='{"q":"甲"}'), ctx_for(OWNER), task_id="t1")
    assert result.ok and seen == {"q": "甲"}


async def test_v2_handler_receives_execution_context():
    registry = ToolRegistry()
    seen = {}

    async def handler(exec_ctx):
        seen["task"] = exec_ctx.task_id
        seen["call"] = exec_ctx.call_id
        seen["tool"] = exec_ctx.tool_name
        seen["owner"] = exec_ctx.owner
        seen["cancelled"] = exec_ctx.run.cancelled
        return ToolExecutionResult.success({"echo": True})

    await reg_add(registry, "probe2", handler=handler, wants_context=True)
    service = make_service(registry=registry)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="call-9", name="probe2", arguments="{}"), ctx_for(OWNER), task_id="task-1")
    assert result.ok and result.content == {"echo": True}
    assert seen == {"task": "task-1", "call": "call-9", "tool": "probe2", "owner": "t.owner", "cancelled": False}


async def test_tool_timeout_yields_failure():
    registry = ToolRegistry()

    async def slow(params):
        await asyncio.sleep(5)

    await reg_add(registry, "slow", handler=slow)
    service = make_service(registry=registry, tool_timeout=0.1)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="c1", name="slow", arguments="{}"), ctx_for(OWNER), task_id="t1")
    assert result.error == "tool_timeout"


async def test_cancel_event_aborts_execution():
    registry = ToolRegistry()

    async def slow(params):
        await asyncio.sleep(5)
        return "不应到达"

    await reg_add(registry, "slow", handler=slow)
    service = make_service(registry=registry, tool_timeout=5)
    service.list_for(ctx_for(OWNER))
    ctx = ctx_for(OWNER)

    async def cancel_soon():
        await asyncio.sleep(0.1)
        ctx.cancel.set()

    cancel_task = asyncio.create_task(cancel_soon())
    result = await service.execute(ToolCall(id="c1", name="slow", arguments="{}"), ctx, task_id="t1")
    await cancel_task
    assert result.error == "cancelled"


async def test_handler_exception_becomes_tool_error():
    registry = ToolRegistry()

    async def boom(params):
        raise RuntimeError("炸了")

    await reg_add(registry, "boom", handler=boom)
    service = make_service(registry=registry)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="c1", name="boom", arguments="{}"), ctx_for(OWNER), task_id="t1")
    assert result.error == "tool_error" and "RuntimeError" in result.content["detail"]


# ---- 结果裁剪与大小限制 ----


async def test_result_clipped_but_structure_valid():
    big = {"items": [{"title": f"条目{i}", "body": "长" * 500} for i in range(50)]}
    clipped = clip_result(big, 3000)
    encoded = json.dumps(clipped, ensure_ascii=False)
    assert len(encoded) <= 3000
    assert isinstance(clipped, dict) and isinstance(clipped.get("items"), list)  # 结构保持


async def test_result_clip_fallback_keeps_envelope():
    scalar = "x" * 100000
    clipped = clip_result({"data": scalar}, 500)
    encoded = json.dumps(clipped, ensure_ascii=False)
    assert len(encoded) <= 700
    assert isinstance(clipped, dict)


async def test_service_clips_oversized_result():
    registry = ToolRegistry()

    async def big_handler(params):
        return {"blob": "字" * 100000}

    await reg_add(registry, "big", handler=big_handler)
    service = make_service(registry=registry, max_result_chars=1000)
    service.list_for(ctx_for(OWNER))
    result = await service.execute(ToolCall(id="c1", name="big", arguments="{}"), ctx_for(OWNER), task_id="t1")
    assert len(json.dumps(result.content, ensure_ascii=False)) <= 1400


# ---- 名称映射 ----


def test_name_mapping_and_collision():
    a = ToolDescriptor(name="web.search", description="", parameters={}, owner="x")
    b = ToolDescriptor(name="web__search", description="", parameters={}, owner="x")
    forward, backward = build_name_mapping([a])
    assert forward["web.search"] == "web__search" and backward["web__search"] == "web.search"
    with pytest.raises(ToolSchemaError, match="映射冲突"):
        build_name_mapping([a, b])  # web.search 与 web_search 都映射为 web__search


def test_model_name_validation():
    bad = ToolDescriptor(name="中文工具", description="", parameters={}, owner="x")
    with pytest.raises(ToolSchemaError, match="无法映射"):
        build_name_mapping([bad])


# ---- Schema 注册校验 ----


@pytest.mark.parametrize(
    "schema, match",
    [
        ({"type": "array"}, "type 必须是 'object'"),
        ({"type": "object"}, "必须显式声明 properties"),
        (None, "必须是 JSON 对象"),
        ({"type": "object", "properties": {"a": {"type": "object"}}, "required": []}, "嵌套对象"),
        ({"type": "object", "properties": {"a": {"type": "string", "minLength": 2}}, "required": []}, "不支持字段"),
        ({"type": "object", "$ref": "#/x", "properties": {"a": {"type": "string"}}}, "不支持的顶层字段"),
        ({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]}, "未声明属性"),
        ({"type": "object", "properties": {"a": {"type": "string", "enum": []}}, "required": []}, "enum"),
    ],
)
def test_validate_schema_rejects_unsupported(schema, match):
    with pytest.raises(ToolSchemaError, match=match):
        validate_schema(schema, tool_name="t")


def test_validate_schema_accepts_flat_object():
    validate_schema(
        {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "查询", "enum": ["a", "b"]}, "n": {"type": "integer"}},
            "required": ["q"],
        },
        tool_name="t",
    )


# ---- 任务清理登记 ----


async def test_cleanup_attacher_wired_to_sink():
    registry = ToolRegistry()
    attached = {}

    async def handler(exec_ctx):
        exec_ctx.attach_cleanup(lambda: None)
        return "ok"

    await reg_add(registry, "dirty", handler=handler, wants_context=True)
    service = make_service(
        registry=registry,
        cleanup_sink=lambda task_id, fn: attached.setdefault(task_id, fn),
    )
    service.list_for(ctx_for(OWNER))
    await service.execute(ToolCall(id="c1", name="dirty", arguments="{}"), ctx_for(OWNER), task_id="task-77")
    assert "task-77" in attached

"""统一工具执行服务：权限、校验、超时、取消与结构化结果。

与 ``ToolRegistry`` 分工：注册表只管登记/归属/查询/撤销；本模块在真正
执行前后做框架级检查——身份能力、插件启停状态、注册有效性、参数解析
与 Schema 校验、单次超时、取消竞速、结果裁剪与大小限制。

执行前校验顺序（防止模型拿着旧工具列表执行已停用插件）：
1. 模型名解析回注册表（unknown_tool）；
2. 归属插件仍处于启用状态（plugin_disabled）；
3. 发起者具备工具要求的能力（permission_denied）；
4. 参数 JSON 解析与 Schema 校验（invalid_arguments 等）；
5. 带 timeout 与取消信号执行；异常转为 tool_error，不外抛中断循环。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from companion.core.permissions import Capability, PermissionDenied, RunContext
from companion.core.tools.registry import ToolDescriptor, ToolRegistry

logger = logging.getLogger(__name__)

#: 模型 API 的函数命名规则（OpenAI 兼容：字母/数字/下划线/连字符）
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")

#: 参数原始 JSON 文本上限
MAX_ARGUMENT_BYTES = 32 * 1024
#: 单个字符串裁剪上限（结果裁剪第一轮）
_PER_STRING_CAP = 2000
_SCALAR_TYPES = ("string", "integer", "number", "boolean", "array")
_MAX_PROPERTIES = 16


class ToolSchemaError(Exception):
    """工具 Schema 不受支持或非法（在注册时拒绝）。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str  # 模型侧名称
    arguments: str  # 原始 JSON 文本（可能非法/截断）


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """v2 工具处理器收到的执行上下文（wants_context=True 时）。

    ``params`` 是经 Schema 校验后的参数对象；``run`` 携带已验证的发起
    者身份、来源会话与取消信号。"""

    run: RunContext
    task_id: str
    call_id: str
    tool_name: str  # 注册表名称
    owner: str  # 归属插件 ID
    params: dict[str, Any] = field(default_factory=dict)
    _cleanup_attacher: Any = None  # Callable[[Callable[[], Awaitable[None]]], None] | None

    def attach_cleanup(self, fn: "Callable[[], Awaitable[None]]") -> None:
        """登记任务级清理回调：任务进入终态（完成/取消/超时/失败）时执行。

        浏览器等跨轮资源用它保证取消与超时后也被释放；未接线时为空操作。
        """
        if self._cleanup_attacher is not None:
            self._cleanup_attacher(fn)


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """工具执行的结构化结果；content 是面向模型的 JSON 值。"""

    ok: bool
    content: Any = None
    error: str | None = None  # ok=False 时的错误码（unknown_tool 等）

    @classmethod
    def success(cls, content: Any) -> "ToolExecutionResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(cls, error: str, detail: str = "") -> "ToolExecutionResult":
        return cls(ok=False, content={"error": error, "detail": detail} if detail else {"error": error}, error=error)


#: v2 处理器：接收执行上下文，返回 ToolExecutionResult 或任意 JSON 值
ContextToolHandler = Callable[[ToolExecutionContext], Awaitable[Any]]
#: 插件启停状态检查（宿主提供：插件 ID -> 是否启用）
OwnerStateChecker = Callable[[str], bool]


# ---------------------------------------------------------------------------
# Schema 校验（注册时整体校验；执行时校验参数值）
# ---------------------------------------------------------------------------

def validate_schema(schema: Any, *, tool_name: str = "") -> None:
    """v1 支持扁平 object Schema：标量/数组属性 + required + enum + description。

    不支持的结构（嵌套 object、$ref、anyOf、minLength 等）在注册时明确拒
    绝，而不是执行时静默失败。
    """
    where = f"工具 {tool_name or '?'} 的 parameters"
    if not isinstance(schema, dict):
        raise ToolSchemaError(f"{where}必须是 JSON 对象")
    allowed_top = {"type", "properties", "required", "description"}
    unsupported = set(schema) - allowed_top
    if unsupported:
        raise ToolSchemaError(
            f"{where}包含不支持的顶层字段：{sorted(unsupported)}；"
            f"v1 仅支持 {sorted(allowed_top)}"
        )
    if schema.get("type") != "object":
        raise ToolSchemaError(f"{where}的 type 必须是 'object'")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ToolSchemaError(f"{where}必须显式声明 properties（无参数工具用空对象）")
    if len(properties) > _MAX_PROPERTIES:
        raise ToolSchemaError(f"{where}属性数超过 {_MAX_PROPERTIES}")
    for key, spec in properties.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ToolSchemaError(f"{where}的属性名非法：{key!r}")
        if not isinstance(spec, dict):
            raise ToolSchemaError(f"{where}的属性 {key} 必须是对象")
        field_type = spec.get("type")
        if field_type not in _SCALAR_TYPES:
            raise ToolSchemaError(
                f"{where}的属性 {key} 类型必须是 {'/'.join(_SCALAR_TYPES)}（嵌套对象 v1 不支持），收到 {field_type!r}"
            )
        spec_allowed = {"type", "description", "enum"}
        bad = set(spec) - spec_allowed
        if bad:
            raise ToolSchemaError(f"{where}的属性 {key} 包含不支持字段：{sorted(bad)}")
        if "enum" in spec:
            enum = spec["enum"]
            if not isinstance(enum, list) or not enum or any(isinstance(v, (dict, list)) for v in enum):
                raise ToolSchemaError(f"{where}的属性 {key} 的 enum 必须是非空标量列表")
        if "description" in spec and (not isinstance(spec["description"], str) or len(spec["description"]) > 256):
            raise ToolSchemaError(f"{where}的属性 {key} 的 description 非法")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
        raise ToolSchemaError(f"{where}的 required 必须是字符串列表")
    unknown_required = [r for r in required if r not in properties]
    if unknown_required:
        raise ToolSchemaError(f"{where}的 required 引用了未声明属性：{unknown_required}")


def _value_matches(field_type: str, value: Any) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "array":
        return isinstance(value, list)
    return False


def validate_arguments(raw: str | None, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """解析并校验模型参数。返回 (params, None) 或 (None, 错误说明)。"""
    text = (raw or "").strip()
    if not text:
        text = "{}"
    if len(text.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        return None, f"参数超过 {MAX_ARGUMENT_BYTES} 字节上限"
    try:
        params = json.loads(text)  # 只用 json 解析，绝不 eval
    except json.JSONDecodeError as exc:
        return None, f"参数不是合法 JSON：{exc.msg}"
    if not isinstance(params, dict):
        return None, "参数必须是 JSON 对象"
    properties = schema.get("properties") or {}
    for key in schema.get("required", []):
        if key not in params:
            return None, f"缺少必填参数 {key}"
    for key, value in params.items():
        spec = properties.get(key)
        if spec is None:
            continue  # 未声明的额外键放行（模型常附带），交给处理器自行忽略
        if not _value_matches(spec["type"], value):
            return None, f"参数 {key} 要求类型 {spec['type']}，收到 {type(value).__name__}"
        if "enum" in spec and value not in spec["enum"]:
            return None, f"参数 {key} 的取值不在允许范围内"
    return params, None


# ---------------------------------------------------------------------------
# 结果裁剪：保持 JSON 结构有效，不截断序列化文本
# ---------------------------------------------------------------------------

def _truncate_strings(value: Any, cap: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + "…（已截断）"
    if isinstance(value, list):
        return [_truncate_strings(item, cap) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_strings(item, cap) for key, item in value.items()}
    return value


def _prune_longest_list(value: Any) -> tuple[Any, bool]:
    """删掉最长列表的最后一项并加标记；返回 (新值, 是否发生裁剪)。"""
    if isinstance(value, list):
        if len(value) > 1:
            return value[:-1], True
        pruned_any = False
        new_items = []
        for item in value:
            new_item, pruned = _prune_longest_list(item)
            new_items.append(new_item)
            pruned_any = pruned_any or pruned
        return new_items, pruned_any
    if isinstance(value, dict):
        best_key, best_len = None, -1
        for key, item in value.items():
            if isinstance(item, list) and len(item) > best_len:
                best_key, best_len = key, len(item)
        if best_key is not None and best_len > 1:
            pruned = dict(value)
            pruned[best_key] = pruned[best_key][:-1]
            pruned["_truncated"] = True
            return pruned, True
        out = {}
        pruned_any = False
        for key, item in value.items():
            new_item, pruned = _prune_longest_list(item)
            out[key] = new_item
            pruned_any = pruned_any or pruned
        return out, pruned_any
    return value, False


def clip_result(value: Any, max_chars: int) -> Any:
    """把结果裁剪到序列化后不超过 max_chars，同时保持 JSON 结构有效。"""
    clipped = _truncate_strings(value, _PER_STRING_CAP)
    while True:
        encoded = json.dumps(clipped, ensure_ascii=False, default=str)
        if len(encoded) <= max_chars:
            return clipped
        clipped, pruned = _prune_longest_list(clipped)
        if not pruned:
            # 再无列表可裁：退化为文本预览，但保留结果外壳结构
            return {"truncated": True, "preview": encoded[:max_chars]}


# ---------------------------------------------------------------------------
# 名称映射：注册表名（允许点号）-> 模型函数名（字母/数字/_/-）
# ---------------------------------------------------------------------------

def build_name_mapping(descriptors: list[ToolDescriptor]) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (registry->model, model->registry)；冲突时抛 ToolSchemaError。"""
    forward: dict[str, str] = {}
    backward: dict[str, str] = {}
    for descriptor in descriptors:
        model_name = descriptor.name.replace(".", "__")
        if not _MODEL_NAME_RE.match(model_name):
            raise ToolSchemaError(f"工具名 {descriptor.name!r} 无法映射为合法模型函数名（{model_name!r}）")
        if model_name in backward:
            raise ToolSchemaError(
                f"工具名映射冲突：{backward[model_name]!r} 与 {descriptor.name!r} 都映射为 {model_name!r}"
            )
        forward[descriptor.name] = model_name
        backward[model_name] = descriptor.name
    return forward, backward


# ---------------------------------------------------------------------------
# 执行服务
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionService:
    """框架级工具执行入口；插件宿主登记、本服务执行。"""

    registry: ToolRegistry
    is_owner_active: OwnerStateChecker | None = None
    tool_timeout: float = 60.0
    max_result_chars: int = 6000
    cleanup_sink: Any = None  # Callable[[task_id, cleanup_fn], None]，任务管理器提供

    _model_to_registry: dict[str, str] = field(default_factory=dict, init=False)

    def list_for(self, ctx: RunContext) -> list[tuple[str, ToolDescriptor]]:
        """当前身份可见（能力允许 + 归属插件启用）的工具，返回 (模型名, 描述)。"""
        visible: list[ToolDescriptor] = []
        for descriptor in self.registry.list_tools():
            if self.is_owner_active is not None and not self.is_owner_active(descriptor.owner):
                continue
            if descriptor.required_capability is not None:
                required = Capability(descriptor.required_capability)
                if required not in ctx.actor.capabilities:
                    continue
            visible.append(descriptor)
        forward, backward = build_name_mapping(visible)
        self._model_to_registry = backward
        return [(forward[d.name], d) for d in visible]

    def tools_payload(self, ctx: RunContext) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": model_name,
                    "description": descriptor.description,
                    "parameters": dict(descriptor.parameters),
                },
            }
            for model_name, descriptor in self.list_for(ctx)
        ]

    async def execute(self, call: ToolCall, ctx: RunContext, *, task_id: str) -> ToolExecutionResult:
        registry_name = self._model_to_registry.get(call.name)
        if registry_name is None:
            # 可能是启停间隙的旧名单——重新解析一次仍未知才判 unknown_tool
            descriptor = self.registry.get(call.name)
            registry_name = call.name if descriptor is not None else None
        descriptor = self.registry.get(registry_name) if registry_name else None
        if descriptor is None:
            return ToolExecutionResult.failure("unknown_tool", call.name)
        if self.is_owner_active is not None and not self.is_owner_active(descriptor.owner):
            return ToolExecutionResult.failure("plugin_disabled", descriptor.owner)
        if descriptor.required_capability is not None:
            try:
                ctx.require(Capability(descriptor.required_capability))
            except PermissionDenied as exc:
                return ToolExecutionResult.failure("permission_denied", str(exc))
        params, error = validate_arguments(call.arguments, descriptor.parameters)
        if error is not None:
            return ToolExecutionResult.failure("invalid_arguments", error)

        executor = self.registry.executor(descriptor.name)
        assert executor is not None  # 注册表条目必带处理器
        attacher = None
        if self.cleanup_sink is not None:
            def attacher(fn: Callable[[], Awaitable[None]]) -> None:
                self.cleanup_sink(task_id, fn)

        exec_ctx = ToolExecutionContext(
            run=ctx,
            task_id=task_id,
            call_id=call.id,
            tool_name=descriptor.name,
            owner=descriptor.owner,
            params=params or {},
            _cleanup_attacher=attacher,
        )
        try:
            if descriptor.wants_context:
                task = asyncio.create_task(executor(exec_ctx))
            else:  # M4 签名：仅参数。wants_context 由注册时显式声明，不猜测签名
                task = asyncio.create_task(executor(params))
            waiter = asyncio.ensure_future(ctx.cancel.wait())
            try:
                done, _ = await asyncio.wait(
                    {task, waiter},
                    timeout=self.tool_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                waiter.cancel()  # 避免悬挂的等待协程
        except Exception as exc:  # 创建/等待阶段的意外错误
            logger.exception("工具执行异常: tool=%s", descriptor.name)
            return ToolExecutionResult.failure("tool_error", f"{type(exc).__name__}: {exc}")
        if task in done:
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "工具 %s（owner=%s）执行失败: %s: %s", descriptor.name, descriptor.owner, type(exc).__name__, exc,
                    exc_info=exc,
                )
                return ToolExecutionResult.failure("tool_error", f"{type(exc).__name__}: {exc}")
            result = _normalize(task.result())
            clipped = clip_result(result.content, self.max_result_chars)
            if clipped is not result.content:
                result = ToolExecutionResult(ok=result.ok, content=clipped, error=result.error)
            return result
        # 取消或超时先到：终止任务并给出明确结果
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if ctx.cancel.is_set():
            return ToolExecutionResult.failure("cancelled", "任务已取消，工具中止")
        return ToolExecutionResult.failure("tool_timeout", f"工具超过 {self.tool_timeout:.0f}s 未完成")


def _normalize(value: Any) -> ToolExecutionResult:
    if isinstance(value, ToolExecutionResult):
        return value
    return ToolExecutionResult.success(value)


def clip_execution_result(result: ToolExecutionResult, max_chars: int) -> ToolExecutionResult:
    """把结果内容裁剪到模型输入预算内（结构保持）。"""
    return ToolExecutionResult(ok=result.ok, content=clip_result(result.content, max_chars), error=result.error)

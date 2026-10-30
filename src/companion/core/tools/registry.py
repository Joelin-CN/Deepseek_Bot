"""统一工具注册表。

与插件宿主分离：这里只负责工具描述的登记、归属、查询与撤销；谁登记
（插件宿主、内核或测试）由调用方决定。统一执行循环（参数校验、超时、
取消、结构化结果、权限与配额）属于 M5，本轮不实现。

工具名全局唯一；每个条目记录归属（owner，通常是插件 ID），停用插件
时按归属批量撤销。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

logger = logging.getLogger(__name__)

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
MAX_TOOL_NAME_LEN = 64


class ToolConflictError(Exception):
    """工具名已被占用。"""


class ToolNameError(Exception):
    """工具名非法。"""


@dataclass(frozen=True)
class ToolDescriptor:
    """对模型可见的工具描述。

    - ``required_capability``：执行所需能力（None 表示基础 CHAT）；
    - ``wants_context``：True 为 v2 处理器（接收 ToolExecutionContext），
      False 为 M4 签名（仅参数 dict）。注册时显式声明，执行时不猜测。
    """

    name: str
    description: str
    parameters: Mapping[str, Any]  # JSON-Schema 风格（v1 扁平 object，注册时校验）
    owner: str  # 登记方：插件 ID 或 "core"
    required_capability: str | None = None
    wants_context: bool = False


ToolExecutor = Callable[..., Awaitable[Any]]


def valid_tool_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_TOOL_NAME_LEN
        and bool(_TOOL_NAME_RE.match(name))
    )


class ToolRegistry:
    """工具登记、查询与按归属撤销。"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDescriptor, ToolExecutor]] = {}

    def register(
        self,
        descriptor: ToolDescriptor,
        handler: ToolExecutor,
    ) -> None:
        if not valid_tool_name(descriptor.name):
            raise ToolNameError(f"工具名非法：{descriptor.name!r}")
        from companion.core.tools.execution import ToolSchemaError, validate_schema

        validate_schema(descriptor.parameters, tool_name=descriptor.name)
        if descriptor.name in self._tools:
            owner = self._tools[descriptor.name][0].owner
            raise ToolConflictError(
                f"工具 {descriptor.name} 已由 {owner} 登记"
            )
        self._tools[descriptor.name] = (descriptor, handler)
        logger.info(
            "工具已登记: %s（owner=%s capability=%s v2=%s）",
            descriptor.name, descriptor.owner, descriptor.required_capability, descriptor.wants_context,
        )

    def get(self, name: str) -> ToolDescriptor | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def executor(self, name: str) -> ToolExecutor | None:
        """M5 执行循环使用；M4 仅测试直接验证归属时调用。"""
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def list_tools(self, owner: str | None = None) -> list[ToolDescriptor]:
        descriptors = [entry[0] for entry in self._tools.values()]
        if owner is not None:
            descriptors = [d for d in descriptors if d.owner == owner]
        return list(descriptors)

    def unregister(self, name: str) -> bool:
        """按名撤销；返回是否确实存在。"""
        entry = self._tools.pop(name, None)
        if entry is not None:
            logger.info("工具已撤销: %s（owner=%s）", name, entry[0].owner)
            return True
        return False

    def unregister_owner(self, owner: str) -> list[str]:
        """撤销某归属方登记的全部工具，返回被撤销的名字。"""
        removed = [
            name
            for name, (descriptor, _) in self._tools.items()
            if descriptor.owner == owner
        ]
        for name in removed:
            del self._tools[name]
        if removed:
            logger.info("按归属撤销工具: owner=%s count=%d", owner, len(removed))
        return removed

    def __len__(self) -> int:
        return len(self._tools)

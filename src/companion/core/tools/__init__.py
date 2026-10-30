"""统一工具注册表与执行服务（登记、归属、查询、撤销；执行、校验、超时、取消）。"""

from companion.core.tools.execution import (
    ContextToolHandler,
    ToolCall,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionService,
    ToolSchemaError,
    build_name_mapping,
    clip_result,
    validate_arguments,
    validate_schema,
)
from companion.core.tools.registry import (
    ToolConflictError,
    ToolDescriptor,
    ToolNameError,
    ToolRegistry,
)

__all__ = [
    "ToolDescriptor",
    "ToolRegistry",
    "ToolExecutionService",
    "ToolCall",
    "ToolExecutionContext",
    "ToolExecutionResult",
    "ContextToolHandler",
    "ToolConflictError",
    "ToolNameError",
    "ToolSchemaError",
    "build_name_mapping",
    "clip_result",
    "validate_arguments",
    "validate_schema",
]

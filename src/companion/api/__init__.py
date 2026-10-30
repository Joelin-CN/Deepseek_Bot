"""Companion 原生插件公共 API。

插件代码只应从 ``companion.api`` 导入；本包是稳定门面，内部模块的划
分可能调整，从这里导出的名字保持兼容。``PLUGIN_API_VERSION`` 标识当
前公共接口版本，与框架版本（``companion.__version__``）相互独立。
"""

from companion.api.context import (
    CommandContext,
    CommandHandler,
    MessageHandler,
    PluginContext,
    PluginKV,
    ToolHandler,
)
from companion.api.errors import (
    KVError,
    KVKeyError,
    KVTypeError,
    KVValueTooLarge,
    PluginAPIVersionError,
    PluginError,
    PluginLoadError,
    PluginStateError,
    RegistrationConflict,
    RegistrationError,
)
from companion.api.plugin import Plugin
from companion.api.results import HandlerResult, normalize_result
from companion.api.types import (
    MAX_PLUGIN_ID_LEN,
    PLUGIN_API_VERSION,
    ConfigFieldSpec,
    PluginMetadata,
    api_version_compatible,
    parse_api_version,
    valid_plugin_id,
    valid_version,
)

# 核心类型的稳定再导出：插件处理消息时只需要这几个形状。
from companion.core.events import InboundMessage
from companion.core.permissions import (
    Actor,
    Capability,
    PermissionDenied,
    Role,
    RunContext,
)
from companion.core.sessions.identity import SessionKey
from companion.core.tools.execution import (
    ContextToolHandler,
    ToolCall,
    ToolExecutionContext,
    ToolExecutionResult,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "MAX_PLUGIN_ID_LEN",
    "Plugin",
    "PluginContext",
    "PluginKV",
    "PluginMetadata",
    "ConfigFieldSpec",
    "CommandContext",
    "CommandHandler",
    "MessageHandler",
    "ToolHandler",
    "ContextToolHandler",
    "ToolCall",
    "ToolExecutionContext",
    "ToolExecutionResult",
    "HandlerResult",
    "normalize_result",
    "InboundMessage",
    "RunContext",
    "Actor",
    "Role",
    "Capability",
    "SessionKey",
    "PermissionDenied",
    "PluginError",
    "PluginLoadError",
    "PluginAPIVersionError",
    "PluginStateError",
    "RegistrationError",
    "RegistrationConflict",
    "KVError",
    "KVKeyError",
    "KVTypeError",
    "KVValueTooLarge",
    "api_version_compatible",
    "parse_api_version",
    "valid_plugin_id",
    "valid_version",
]

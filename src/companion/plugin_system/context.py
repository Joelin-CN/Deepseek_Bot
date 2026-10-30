"""PluginContext 的宿主实现。

上下文在启用时创建、停用时失效：失效后所有注册调用抛
``RegistrationError``，已注册资源由宿主统一撤销。上下文自身记录启用期
间发生的一切注册，供启用失败回滚与停用清理使用。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping

from companion.api import (
    CommandHandler,
    MessageHandler,
    PluginContext,
    PluginKV,
    RegistrationError,
    ToolHandler,
)
from companion.core.tools import (
    ToolConflictError,
    ToolDescriptor,
    ToolNameError,
    ToolRegistry,
    ToolSchemaError,
)
from companion.plugin_system.registry import (
    CommandEntry,
    CommandRegistry,
    MessageHandlerRegistry,
)

_REGISTRATION_CLOSED = "插件上下文已失效（停用或启用失败回滚），不再接受注册"


class PluginContextImpl(PluginContext):
    def __init__(
        self,
        *,
        plugin_id: str,
        config: Mapping[str, Any],
        data_dir: Path,
        kv: PluginKV,
        logger: logging.Logger,
        commands: CommandRegistry,
        message_handlers: MessageHandlerRegistry,
        tools: ToolRegistry,
        tasks: "PluginTaskTracker",
    ):
        self._plugin_id = plugin_id
        self._config = dict(config)
        self._data_dir = data_dir
        self._kv = kv
        self._logger = logger
        self._commands = commands
        self._message_handlers = message_handlers
        self._tools = tools
        self._tasks = tasks
        self._active = True
        # 启用期间的注册记录，供回滚/清理
        self._command_names: list[str] = []
        self._handler_count = 0
        self._tool_names: list[str] = []

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    def config(self) -> Mapping[str, Any]:
        return MappingProxy(self._config)

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def kv(self) -> PluginKV:
        return self._kv

    def _require_active(self) -> None:
        if not self._active:
            raise RegistrationError(_REGISTRATION_CLOSED)

    def register_command(
        self,
        name: str,
        handler: CommandHandler,
        *,
        description: str = "",
        usage: str = "",
    ) -> None:
        self._require_active()
        self._commands.register(
            CommandEntry(
                name=name,
                handler=handler,
                owner=self._plugin_id,
                description=description,
                usage=usage,
            )
        )
        self._command_names.append(name)

    def register_message_handler(
        self,
        handler: MessageHandler,
        *,
        name: str = "",
        priority: int = 100,
    ) -> None:
        self._require_active()
        self._message_handlers.register(handler, owner=self._plugin_id, name=name, priority=priority)
        self._handler_count += 1

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Mapping[str, Any],
        handler: ToolHandler,
        *,
        required_capability: Any | None = None,
        wants_context: bool = False,
    ) -> None:
        self._require_active()
        try:
            self._tools.register(
                ToolDescriptor(
                    name=name,
                    description=description,
                    parameters=parameters,
                    owner=self._plugin_id,
                    required_capability=(
                        required_capability.value
                        if hasattr(required_capability, "value")
                        else required_capability
                    ),
                    wants_context=wants_context,
                ),
                handler,
            )
        except ToolConflictError as exc:
            from companion.api import RegistrationConflict

            raise RegistrationConflict(f"工具 {name} 注册冲突：{exc}") from exc
        except ToolNameError as exc:
            raise RegistrationError(str(exc)) from exc
        except ToolSchemaError as exc:
            raise RegistrationError(str(exc)) from exc
        self._tool_names.append(name)

    def create_task(self, coro, *, name: str = "") -> asyncio.Task:
        self._require_active()
        return self._tasks.spawn(self._plugin_id, coro, name=name)

    # ---- 宿主内部接口（不属于公共 API） ----

    @property
    def registered_commands(self) -> list[str]:
        return list(self._command_names)

    @property
    def registered_tools(self) -> list[str]:
        return list(self._tool_names)

    @property
    def registered_handler_count(self) -> int:
        return self._handler_count

    def invalidate(self) -> None:
        """停用/回滚时关闭注册入口。幂等。"""
        self._active = False


class MappingProxy(Mapping[str, Any]):
    """轻量只读视图，避免暴露可变 dict。"""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:  # pragma: no cover - 调试用途
        return f"PluginConfig({self._data!r})"


class PluginTaskTracker:
    """按插件归属跟踪后台任务；异常被观察并记日志，停用时统一取消。"""

    def __init__(self, cancel_timeout: float = 5.0):
        self._tasks: dict[str, set[asyncio.Task]] = {}
        self._cancel_timeout = cancel_timeout

    def spawn(self, plugin_id: str, coro, *, name: str = "") -> asyncio.Task:
        task_name = f"plugin-{plugin_id}:{name}" if name else f"plugin-{plugin_id}"
        task = asyncio.create_task(coro, name=task_name)

        def _done(t: asyncio.Task) -> None:
            tasks = self._tasks.get(plugin_id)
            if tasks is not None:
                tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logging.getLogger(f"companion.plugin.{plugin_id}").error(
                    "插件后台任务异常退出: task=%s error=%r", t.get_name(), exc,
                    exc_info=exc,
                )

        task.add_done_callback(_done)
        self._tasks.setdefault(plugin_id, set()).add(task)
        return task

    async def cancel_owner(self, plugin_id: str) -> int:
        """取消并等待该插件全部后台任务；超时只告警，不无限阻塞。"""
        tasks = list(self._tasks.get(plugin_id, ()))
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._cancel_timeout)
            if pending:
                logging.getLogger(f"companion.plugin.{plugin_id}").warning(
                    "插件后台任务取消超时（%.1fs），%d 个任务仍在运行",
                    self._cancel_timeout,
                    len(pending),
                )
        self._tasks.pop(plugin_id, None)
        return len(tasks)

    def count(self, plugin_id: str) -> int:
        return len(self._tasks.get(plugin_id, ()))

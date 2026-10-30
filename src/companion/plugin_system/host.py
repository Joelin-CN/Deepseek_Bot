"""插件宿主：生命周期编排、消息派发与主人管理命令。

生命周期语义（加载 ≠ 启用）：

- **加载**：校验清单 → 依赖检查 → 导入入口模块 → 实例化。不执行任何
  插件逻辑，不注册任何资源。
- **启用**：合并并校验配置 → 建数据目录 → 创建 PluginContext → 调用
  ``on_enable``（插件在此注册命令/处理器/工具/任务）。异常则回滚本次
  全部注册，插件停在 DISABLED 并记录原因。
- **停用**：先调 ``on_disable``（异常只记日志），随后无论如何撤销该插
  件的命令/处理器/工具注册并取消其后台任务（有界等待）。停用后注册表
  中不再有该插件条目，新消息不会派发给它；正在执行中的处理器调用自然
  结束。
- **卸载**：宿主语义是"撤销注册 + 释放资源 + 丢弃实例"；不做 Python
  模块卸载，不支持热更新。

启用状态持久化规则：显式管理操作（/plugin enable|disable）写入
``plugin_runtime_state`` 表；启动时该表行优先于配置文件
``plugins.instances.<id>.enabled``（缺省停用）；进程退出不改写该表。

派发顺序（在会话锁内、记忆命令之前）：

1. ``/plugin`` 管理命令（仅主人，恒消费）；
2. 插件消息处理器（priority 升序，消费即止，异常跳过）；
3. 插件命令 ``/name args``（匹配即消费；处理器异常回复错误提示）；
4. 均未消费 → 返回 None，交给记忆命令与模型。

同进程 Python 插件按可信本机代码运行：命名空间与权限声明是框架管理
机制，不是操作系统级隔离；插件可以阻塞事件循环或直接操作主机。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from companion.api import (
    HandlerResult,
    InboundMessage,
    PluginError,
    PluginStateError,
    Role,
    RunContext,
    normalize_result,
)
from companion.core.tools import ToolRegistry
from companion.persistence import Database
from companion.plugin_system.context import PluginContextImpl, PluginTaskTracker
from companion.plugin_system.kv import PluginKVStore, PluginRuntimeStateStore
from companion.plugin_system.loader import discover_plugins, load_plugin
from companion.plugin_system.registry import CommandRegistry, MessageHandlerRegistry

logger = logging.getLogger(__name__)

MAX_CONFIG_VALUE_BYTES = 16 * 1024
MAX_CONFIG_TOTAL_BYTES = 64 * 1024

_MANAGEMENT_USAGE = (
    "用法：/plugin list | /plugin status <插件ID> | "
    "/plugin enable <插件ID> | /plugin disable <插件ID>"
)


class PluginState(str, Enum):
    FAILED = "FAILED"  # 加载或启用失败，保留错误原因
    DISABLED = "DISABLED"  # 已加载未启用（从未启用或停用/回滚后）
    ENABLED = "ENABLED"


@dataclass
class _PluginRecord:
    directory: Path
    state: PluginState = PluginState.DISABLED
    error: str | None = None
    plugin_id: str | None = None  # 加载失败（无清单 ID）时为 None
    name: str = ""
    version: str = ""
    metadata: Any | None = None
    instance: Any | None = None
    context: PluginContextImpl | None = None
    enabled_source: str = "default"  # "runtime" | "config" | "default"

    @property
    def key(self) -> str:
        return self.plugin_id if self.plugin_id else f"@{self.directory.name}"


@dataclass(frozen=True)
class PluginStatus:
    plugin_id: str
    name: str
    version: str
    state: str
    error: str | None
    enabled_source: str


class PluginHost:
    def __init__(
        self,
        *,
        roots: list[Path],
        db: Database,
        tools: ToolRegistry,
        data_dir: Path,
        instance_settings: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self._roots = [Path(root) for root in roots]
        self._db = db
        self._tools = tools
        self._instance_settings: dict[str, dict[str, Any]] = {
            pid: dict(entry) for pid, entry in (instance_settings or {}).items()
        }
        self._kv_store = PluginKVStore(db)
        self._state_store = PluginRuntimeStateStore(db)
        self._commands = CommandRegistry()
        self._message_handlers = MessageHandlerRegistry()
        self._tasks = PluginTaskTracker()
        self._data_base = (Path(data_dir) / "plugins" / "data").resolve()
        self._records: dict[str, _PluginRecord] = {}
        self._started = False

    # ---- 启动与关停 ----

    async def startup(self) -> None:
        """发现并加载全部插件，按持久化/配置状态启用。幂等（只允许一次）。"""
        if self._started:
            raise PluginStateError("PluginHost.startup 只能调用一次")
        self._started = True
        for directory in discover_plugins(self._roots):
            record = _PluginRecord(directory=directory)
            try:
                loaded = load_plugin(directory)
            except PluginError as exc:
                record.state = PluginState.FAILED
                record.error = f"加载失败：{exc}"
                self._records[record.key] = record
                logger.error("插件加载失败: dir=%s reason=%s", directory, exc)
                continue
            if loaded.metadata.plugin_id in self._records:
                record.state = PluginState.FAILED
                record.error = (
                    f"加载失败：重复的插件 ID {loaded.metadata.plugin_id}，"
                    f"已由 {self._records[loaded.metadata.plugin_id].directory} 占用"
                )
                self._records[record.key] = record
                logger.error("跳过重复插件 ID: dir=%s", directory)
                continue
            record.plugin_id = loaded.metadata.plugin_id
            record.name = loaded.metadata.name
            record.version = loaded.metadata.version
            record.metadata = loaded.metadata
            record.instance = loaded.instance
            self._records[record.plugin_id] = record

        for plugin_id in sorted(r.plugin_id for r in self._records.values() if r.plugin_id):
            record = self._records[plugin_id]
            runtime = await self._state_store.get(plugin_id)
            entry = self._instance_settings.get(plugin_id, {})
            if runtime is not None:
                should_enable, record.enabled_source = runtime, "runtime"
            else:
                should_enable = bool(entry.get("enabled", False))
                record.enabled_source = "config" if should_enable else "default"
            if should_enable:
                ok, message = await self.enable(plugin_id, persist=False)
                if ok:
                    logger.info("启动时启用插件: %s", plugin_id)
                else:
                    logger.error("启动时启用插件失败: %s reason=%s", plugin_id, message)
        logger.info(
            "插件宿主就绪: 共 %d 个插件目录，启用 %d 个",
            len(self._records),
            sum(1 for r in self._records.values() if r.state is PluginState.ENABLED),
        )

    async def shutdown(self) -> None:
        """停用全部已启用插件（不写运行状态表，重启按既有规则恢复）。"""
        for plugin_id in sorted(
            r.plugin_id for r in self._records.values() if r.plugin_id
        ):
            record = self._records[plugin_id]
            if record.state is PluginState.ENABLED:
                ok, message = await self.disable(plugin_id, persist=False)
                logger.info("退出时停用插件 %s: %s", plugin_id, message if ok else f"失败 {message}")
        logger.info("插件宿主已关停")

    # ---- 启用 / 停用 ----

    async def enable(self, plugin_id: str, *, persist: bool = True) -> tuple[bool, str]:
        record = self._records.get(plugin_id)
        if record is None or record.plugin_id is None:
            return False, f"未发现插件 {plugin_id}（检查 plugins 目录与 plugins.extra_roots 配置）"
        if record.state is PluginState.ENABLED:
            return True, "已处于启用状态"
        if record.instance is None:
            try:
                loaded = load_plugin(record.directory)
            except PluginError as exc:
                record.state = PluginState.FAILED
                record.error = f"加载失败：{exc}"
                return False, record.error
            record.instance = loaded.instance
            record.metadata = loaded.metadata
            record.name = loaded.metadata.name
            record.version = loaded.metadata.version
        metadata = record.metadata
        try:
            config = self._build_config(metadata, self._instance_settings.get(plugin_id, {}).get("config", {}))
        except PluginError as exc:
            record.state = PluginState.DISABLED
            record.error = f"配置非法：{exc}"
            return False, record.error
        try:
            data_dir = self._safe_data_dir(plugin_id)
        except PluginStateError as exc:
            record.state = PluginState.DISABLED
            record.error = str(exc)
            return False, record.error

        plugin_logger = logging.getLogger(f"companion.plugin.{plugin_id}")
        context = PluginContextImpl(
            plugin_id=plugin_id,
            config=config,
            data_dir=data_dir,
            kv=self._kv_store.bind(plugin_id),
            logger=plugin_logger,
            commands=self._commands,
            message_handlers=self._message_handlers,
            tools=self._tools,
            tasks=self._tasks,
        )
        record.context = context
        try:
            await record.instance.on_enable(context)
        except Exception as exc:
            plugin_logger.exception("插件启用失败，回滚本次注册")
            await self._cleanup(record)
            record.state = PluginState.DISABLED
            record.error = f"启用失败：{type(exc).__name__}: {exc}"
            return False, record.error
        record.state = PluginState.ENABLED
        record.error = None
        if persist:
            await self._state_store.set(plugin_id, True)
        logger.info(
            "插件已启用: %s（命令 %d 个，工具 %d 个）",
            plugin_id, len(context.registered_commands), len(context.registered_tools),
        )
        return True, "已启用"

    async def disable(self, plugin_id: str, *, persist: bool = True) -> tuple[bool, str]:
        record = self._records.get(plugin_id)
        if record is None:
            return False, f"未发现插件 {plugin_id}"
        if record.state is not PluginState.ENABLED:
            if persist:  # 已停用时也记录意图，使配置默认启用在重启后保持停用
                await self._state_store.set(plugin_id, False)
            return True, "已处于停用状态"
        if record.instance is not None and record.context is not None:
            try:
                await record.instance.on_disable(record.context)
            except Exception:
                record.context.logger.exception("插件 on_disable 异常（继续强制清理）")
        await self._cleanup(record)
        record.state = PluginState.DISABLED
        if persist:
            await self._state_store.set(plugin_id, False)
        return True, "已停用"

    async def _cleanup(self, record: _PluginRecord) -> None:
        plugin_id = record.plugin_id or record.key
        if record.context is not None:
            record.context.invalidate()
        commands_removed = self._commands.unregister_owner(plugin_id)
        handlers_removed = self._message_handlers.unregister_owner(plugin_id)
        tools_removed = self._tools.unregister_owner(plugin_id)
        tasks_cancelled = await self._tasks.cancel_owner(plugin_id)
        record.context = None
        logger.info(
            "已清理插件资源: %s（命令 %d，处理器 %d，工具 %d，任务 %d）",
            plugin_id, len(commands_removed), handlers_removed, len(tools_removed), tasks_cancelled,
        )

    # ---- 消息派发 ----

    async def handle_message(
        self, ctx: RunContext, message: InboundMessage
    ) -> HandlerResult | None:
        """插件阶段派发；返回 None 表示未消费（继续记忆命令/模型）。"""
        if message.session.chat_type != "private":
            return None  # 群聊接入（M6）前插件只处理私聊
        text = message.text.strip()
        if text.startswith("/plugin"):
            head = text.split(None, 1)[0]
            if head == "/plugin":
                return await self._handle_management(ctx, text)
        handler_result = await self._run_message_handlers(ctx, message)
        if handler_result is not None:
            return handler_result
        if text.startswith("/") and len(text) > 1:
            parts = text[1:].split(None, 1)
            name = parts[0]
            args = parts[1].strip() if len(parts) > 1 else ""
            entry = self._commands.get(name)
            if entry is not None:
                return await self._run_command(entry, ctx, message, name, args)
        return None

    async def _run_message_handlers(
        self, ctx: RunContext, message: InboundMessage
    ) -> HandlerResult | None:
        for entry in self._message_handlers.entries():  # 已按 (priority, seq) 排序
            if ctx.cancelled:
                return None
            try:
                result = normalize_result(await entry.handler(ctx, message))
            except Exception:
                logging.getLogger(f"companion.plugin.{entry.owner}").exception(
                    "消息处理器 %s 异常（跳过并继续传播）", entry.name
                )
                continue
            if result is not None and result.consumed:
                return result
        return None

    async def _run_command(
        self, entry, ctx: RunContext, message: InboundMessage, name: str, args: str
    ) -> HandlerResult:
        from companion.api import CommandContext

        command_context = CommandContext(
            run=ctx, message=message, command=name, args=args
        )
        try:
            result = normalize_result(await entry.handler(command_context))
        except Exception:
            logging.getLogger(f"companion.plugin.{entry.owner}").exception(
                "命令 /%s 处理异常（插件 %s）", name, entry.owner
            )
            return HandlerResult.reply(
                f"插件 {entry.owner} 处理 /{name} 时出错，已记录日志。"
            )
        if result is None:  # 命令已匹配：消费但不回复
            return HandlerResult.consume()
        return result

    # ---- 管理命令（仅主人） ----

    async def _handle_management(self, ctx: RunContext, text: str) -> HandlerResult:
        if ctx.actor.role != Role.OWNER:
            logger.info(
                "非主人尝试插件管理: user=%s role=%s", ctx.actor.user_id, ctx.actor.role.value
            )
            return HandlerResult.reply("插件管理仅限主人执行。")
        tokens = text.split()
        if len(tokens) < 2:
            return HandlerResult.reply(_MANAGEMENT_USAGE)
        sub = tokens[1].lower()
        if sub == "list":
            return HandlerResult.reply(self._render_list())
        if sub in {"status", "enable", "disable"}:
            if len(tokens) < 3:
                return HandlerResult.reply(f"缺少插件 ID。{_MANAGEMENT_USAGE}")
            plugin_id = tokens[2]
            if sub == "status":
                return HandlerResult.reply(self._render_status(plugin_id))
            if sub == "enable":
                ok, message = await self.enable(plugin_id, persist=True)
                return HandlerResult.reply(f"{'启用成功' if ok else '启用失败'}：{plugin_id} {message}")
            ok, message = await self.disable(plugin_id, persist=True)
            return HandlerResult.reply(f"{'停用成功' if ok else '停用失败'}：{plugin_id} {message}")
        return HandlerResult.reply(_MANAGEMENT_USAGE)

    def _render_list(self) -> str:
        statuses = self.statuses()
        if not statuses:
            return "没有发现任何插件目录。"
        lines = [f"共发现 {len(statuses)} 个插件："]
        for status in statuses:
            line = f"- {status.plugin_id} v{status.version or '?'} [{status.state}]"
            if status.error:
                line += f" {status.error}"
            elif status.name:
                line += f" {status.name}"
            lines.append(line)
        lines.append(_MANAGEMENT_USAGE)
        return "\n".join(lines)

    def _render_status(self, plugin_id: str) -> str:
        record = self._records.get(plugin_id)
        if record is None:
            return f"未发现插件 {plugin_id}。"
        context = record.context
        parts = [
            f"插件：{plugin_id}",
            f"名称：{record.name or '?'}",
            f"版本：{record.version or '?'}",
            f"状态：{record.state.value}（启用来源：{record.enabled_source}）",
        ]
        if context is not None:
            parts.append(
                f"已注册：命令 {len(context.registered_commands)} 个"
                f"（{'/'.join(context.registered_commands) or '无'}），"
                f"工具 {len(context.registered_tools)} 个，"
                f"后台任务 {self._tasks.count(plugin_id)} 个"
            )
        if record.error:
            parts.append(f"错误：{record.error}")
        return "\n".join(parts)

    # ---- 状态查询 ----

    def is_plugin_active(self, plugin_id: str) -> bool:
        """工具执行前的启停复查：插件是否处于 ENABLED（供执行服务防旧名单）。"""
        record = self._records.get(plugin_id)
        return record is not None and record.state is PluginState.ENABLED

    def statuses(self) -> list[PluginStatus]:
        result = []
        for key in sorted(self._records):
            record = self._records[key]
            result.append(
                PluginStatus(
                    plugin_id=record.plugin_id or f"<无法加载:@{record.directory.name}>",
                    name=record.name,
                    version=record.version,
                    state=record.state.value,
                    error=record.error,
                    enabled_source=record.enabled_source,
                )
            )
        return result

    # ---- 配置与数据目录 ----

    def _build_config(self, metadata, provided: Any) -> dict[str, Any]:
        plugin_id = metadata.plugin_id
        if not isinstance(provided, dict):
            raise PluginError(f"插件 {plugin_id} 的 config 必须是对象")
        config: dict[str, Any] = {key: spec.default for key, spec in metadata.config_schema.items()}
        for key, value in provided.items():
            spec = metadata.config_schema.get(key)
            if spec is None:
                raise PluginError(
                    f"插件 {plugin_id} 未声明配置项 {key!r}"
                    f"（清单声明的是：{sorted(metadata.config_schema) or '无'}）"
                )
            if not _value_matches_type(spec.type, value):
                raise PluginError(
                    f"插件 {plugin_id} 配置项 {key} 要求类型 {spec.type}，"
                    f"收到 {type(value).__name__}：{json.dumps(value, ensure_ascii=False, default=str)[:200]}"
                )
            _check_config_value_size(plugin_id, key, value)
            config[key] = value
        _check_config_total_size(plugin_id, config)
        return config

    def _safe_data_dir(self, plugin_id: str) -> Path:
        target = (self._data_base / plugin_id).resolve()
        if not target.is_relative_to(self._data_base):
            raise PluginStateError(
                f"插件数据目录越界：{target} 不在 {self._data_base} 之下（插件 ID 非法）"
            )
        target.mkdir(parents=True, exist_ok=True)
        return target


def _value_matches_type(field_type: str, value: Any) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "bool":
        return isinstance(value, bool)
    if field_type == "list":
        return isinstance(value, list)
    return False


def _check_config_value_size(plugin_id: str, key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > MAX_CONFIG_VALUE_BYTES:
        raise PluginError(
            f"插件 {plugin_id} 配置项 {key} 超过 {MAX_CONFIG_VALUE_BYTES} 字节上限"
        )


def _check_config_total_size(plugin_id: str, config: dict[str, Any]) -> None:
    encoded = json.dumps(config, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > MAX_CONFIG_TOTAL_BYTES:
        raise PluginError(
            f"插件 {plugin_id} 配置总量超过 {MAX_CONFIG_TOTAL_BYTES} 字节上限"
        )

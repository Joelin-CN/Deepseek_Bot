"""命令与消息处理器注册表（带归属，供宿主按插件撤销）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from companion.api import (
    CommandHandler,
    InboundMessage,
    MessageHandler,
    RegistrationConflict,
    RegistrationError,
    RunContext,
)

#: 管理命令占用 "plugin"（插件管理）与 "task"（任务控制，M5），插件命令不得使用
RESERVED_COMMAND_NAMES = frozenset({"plugin", "plugins", "task"})

MAX_COMMAND_NAME_LEN = 32
_COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def valid_command_name(name: str) -> bool:
    return (
        bool(name)
        and len(name) <= MAX_COMMAND_NAME_LEN
        and bool(_COMMAND_NAME_RE.match(name))
    )


@dataclass(frozen=True)
class CommandEntry:
    name: str
    handler: CommandHandler
    owner: str  # 插件 ID
    description: str = ""
    usage: str = ""


@dataclass(frozen=True)
class MessageHandlerEntry:
    handler: MessageHandler
    owner: str
    name: str  # 展示用（日志定位）
    priority: int  # 数值越小越先执行
    seq: int  # 同优先级按注册顺序


class CommandRegistry:
    """全局命令表：名称唯一，条目归属插件。"""

    def __init__(self) -> None:
        self._commands: dict[str, CommandEntry] = {}

    def register(self, entry: CommandEntry) -> None:
        if not valid_command_name(entry.name):
            raise RegistrationError(
                f"命令名非法：{entry.name!r}（小写字母开头，限小写字母/数字/下划线）"
            )
        if entry.name in RESERVED_COMMAND_NAMES:
            raise RegistrationError(f"命令名 {entry.name!r} 为宿主保留")
        existing = self._commands.get(entry.name)
        if existing is not None:
            raise RegistrationConflict(
                f"命令 /{entry.name} 已由插件 {existing.owner} 注册，"
                f"{entry.owner} 无法重复注册"
            )
        self._commands[entry.name] = entry

    def get(self, name: str) -> CommandEntry | None:
        return self._commands.get(name)

    def unregister_owner(self, owner: str) -> list[str]:
        removed = [
            name for name, entry in self._commands.items() if entry.owner == owner
        ]
        for name in removed:
            del self._commands[name]
        return removed

    def entries(self) -> list[CommandEntry]:
        return list(self._commands.values())

    def __len__(self) -> int:
        return len(self._commands)


class MessageHandlerRegistry:
    """消息事件处理器表：按 (priority, seq) 升序执行，支持按归属撤销。"""

    def __init__(self) -> None:
        self._entries: list[MessageHandlerEntry] = []
        self._seq = 0

    def register(
        self,
        handler: MessageHandler,
        *,
        owner: str,
        name: str = "",
        priority: int = 100,
    ) -> None:
        self._seq += 1
        self._entries.append(
            MessageHandlerEntry(
                handler=handler,
                owner=owner,
                name=name or handler.__name__,
                priority=priority,
                seq=self._seq,
            )
        )
        self._entries.sort(key=lambda e: (e.priority, e.seq))

    def entries(self) -> list[MessageHandlerEntry]:
        return list(self._entries)

    def unregister_owner(self, owner: str) -> int:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.owner != owner]
        return before - len(self._entries)

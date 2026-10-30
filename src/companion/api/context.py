"""插件运行上下文的公共接口。

``PluginContext`` 是宿主交给插件的操作面：读取自身配置、日志与数据目
录，注册命令/消息处理器/工具，创建后台任务。接口在 ``companion.api``
声明、由 ``companion.plugin_system`` 实现；插件只依赖本包，不接触宿主
内部结构。

安全边界：

- 命名空间（KV）、数据目录、日志标识都由宿主按插件 ID 绑定，公共接口
  没有任何让插件指定其他插件命名空间的参数；
- 插件收到的是框架构造的 ``RunContext``（已验证的发送者与会话），昵
  称、消息正文、模型参数不能改变身份；
- ``create_task`` 创建的任务归属本插件，停用时统一取消。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from companion.api.results import HandlerResult
from companion.core.events import InboundMessage
from companion.core.permissions import RunContext

#: 命令处理器：返回 str（消费并回复）/ HandlerResult / None（消费但不回复）
CommandHandler = Callable[["CommandContext"], Awaitable[Any]]
#: 消息事件处理器：每个私聊消息都会经过；返回 None/HandlerResult.ignore 表示不消费
MessageHandler = Callable[[RunContext, InboundMessage], Awaitable[HandlerResult | None]]
#: 工具处理器：参数为模型给出的 JSON 对象；统一执行循环在 M5 定义
ToolHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CommandContext:
    """命令处理器收到的调用上下文。"""

    run: RunContext
    message: InboundMessage
    command: str  # 不含斜杠的命令名，如 "hello"
    args: str  # 命令名之后的剩余文本（已去除首尾空白）


class PluginKV(ABC):
    """插件键值存储视图（SQLite 持久化，JSON 值）。

    命名空间由宿主绑定为插件 ID，接口不暴露命名空间参数。限制：键为
    1–128 个字符（字母/数字/``_ . : - /``）；值必须可 JSON 序列化且序
    列化后不超过 64 KiB。超限或不可序列化会抛出 ``KVKeyError`` /
    ``KVValueTooLarge`` / ``KVTypeError``。
    """

    @abstractmethod
    async def get(self, key: str, default: Any = None) -> Any:
        """读取键值；不存在时返回 default。"""

    @abstractmethod
    async def set(self, key: str, value: Any) -> None:
        """写入键值（覆盖旧值）。"""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """删除键；返回是否确实删除了。"""

    @abstractmethod
    async def keys(self) -> list[str]:
        """列出本命名空间的全部键（按字典序）。"""

    @abstractmethod
    async def count(self) -> int:
        """本命名空间的键数量。"""


class PluginContext(ABC):
    """插件在 on_enable/on_disable 期间及运行中可用的操作面。"""

    @property
    @abstractmethod
    def plugin_id(self) -> str: ...

    @property
    @abstractmethod
    def logger(self) -> logging.Logger:
        """本插件专属 logger（companion.plugin.<id>）。"""

    @property
    @abstractmethod
    def config(self) -> Mapping[str, Any]:
        """合并缺省值后校验过的只读配置。"""

    @property
    @abstractmethod
    def data_dir(self) -> Path:
        """本插件专属数据目录（<data>/plugins/data/<id>/，已创建）。"""

    @property
    @abstractmethod
    def kv(self) -> PluginKV: ...

    @abstractmethod
    def register_command(
        self,
        name: str,
        handler: CommandHandler,
        *,
        description: str = "",
        usage: str = "",
    ) -> None:
        """注册命令（触发语法 ``/name 参数``）。

        名称被其他插件占用抛 ``RegistrationConflict``；使用保留名或非法
        名称抛 ``RegistrationError``。在 on_enable 中抛出的异常会导致启用
        失败并回滚本插件已注册的全部资源。
        """

    @abstractmethod
    def register_message_handler(
        self,
        handler: MessageHandler,
        *,
        name: str = "",
        priority: int = 100,
    ) -> None:
        """注册消息事件处理器。

        priority 数值越小越先执行（默认 100）；先到处理器若消费消息，
        后续处理器不再收到。处理器异常只记日志并跳过，不影响后续。
        """

    @abstractmethod
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
        """登记模型可调用的工具描述（JSON-Schema 风格 parameters）。

        - 名称全局唯一，冲突抛 ``RegistrationConflict``；Schema 在登记时
          校验（v1 支持扁平 object：标量/数组属性 + required + enum）。
        - ``wants_context=False``（默认，M4 签名）：handler 只收参数 dict；
          ``wants_context=True``（v2）：handler 收 ``ToolExecutionContext``
          （含已验证的 RunContext、任务/调用 ID、取消信号）。签名版本
          在注册时显式声明，框架不做运行时猜测。
        - ``required_capability``：执行所需能力（如 ``Capability.WEB_BROWSE``）；
          None 表示基础对话能力即可。真正执行前框架会再次校验身份、
          注册状态与插件启停状态。

        统一执行循环、超时与结果限制由框架执行服务负责（M5 起可用）。
        """

    @abstractmethod
    def create_task(self, coro, *, name: str = "") -> asyncio.Task:
        """创建归属本插件的后台任务。

        任务异常会被观察并记入日志，不会静默丢失。插件停用或进程退出
        时任务被取消（宿主有界等待，默认 5 秒）。
        """

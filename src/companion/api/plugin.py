"""原生插件基类。"""

from __future__ import annotations

from abc import ABC

from companion.api.context import PluginContext


class Plugin(ABC):
    """原生插件基类：实现者继承本类并在清单 entry 中指向它。

    生命周期由宿主驱动：加载（导入模块并实例化，不调用任何钩子）→
    启用（调用 ``on_enable``，插件在此注册命令/处理器/工具/任务）→
    停用（宿主先调用 ``on_disable``，随后无论成败都撤销注册并取消任
    务）。

    - ``on_enable`` 抛出任何异常都会使启用失败，宿主回滚本次注册的全
      部资源，插件进入 DISABLED 并记录错误原因；
    - ``on_disable`` 异常只记日志，不影响宿主强制清理；
    - 钩子之外不应长期持有 PluginContext 之外的宿主内部对象。
    """

    async def on_enable(self, ctx: PluginContext) -> None:
        """启用钩子：注册资源、读取配置、启动后台任务。默认无操作。"""

    async def on_disable(self, ctx: PluginContext) -> None:
        """停用钩子：自愿清理（注册与任务由宿主强制撤销）。默认无操作。"""

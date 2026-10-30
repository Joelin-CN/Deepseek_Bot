"""回显示例插件：最小可运行的原生插件。

加载方式（开发示例，不放入内置 plugins/）：在 config/local.json 增加

    "plugins": {
      "extra_roots": ["examples/plugins"],
      "instances": {
        "example.echo": {"enabled": true, "config": {"prefix": "echo:"}}
      }
    }
"""

from __future__ import annotations

from companion.api import CommandContext, Plugin, PluginContext


class EchoPlugin(Plugin):
    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def on_enable(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.register_command(
            "echo",
            self._handle_echo,
            description="原样返回参数",
            usage="/echo <任意文本>",
        )

    async def on_disable(self, ctx: PluginContext) -> None:
        self._ctx = None

    async def _handle_echo(self, cmd: CommandContext) -> str:
        assert self._ctx is not None
        prefix = str(self._ctx.config.get("prefix", "echo:"))
        if not cmd.args:
            return "用法：/echo <任意文本>"
        return f"{prefix}{cmd.args}"

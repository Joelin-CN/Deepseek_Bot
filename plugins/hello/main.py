"""问候插件（内置，M4 示例）。

演示三件事：
- 经 PluginContext 注册命令（/hello）并读取自身配置；
- 从框架给的 RunContext 读取当前会话与已验证身份信息；
- 登记一个真实工具描述（统一执行循环在 M5 接入，本轮只保证登记/撤销）。
"""

from __future__ import annotations

from companion.api import CommandContext, Plugin, PluginContext


class HelloPlugin(Plugin):
    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def on_enable(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.register_command(
            "hello",
            self._handle_hello,
            description="打个招呼并展示当前会话信息",
            usage="/hello",
        )
        ctx.register_tool(
            "hello_greet",
            description="生成一句问候语（工具执行循环在 M5 接入，本轮仅登记演示）",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "称呼，默认'朋友'"}
                },
                "required": [],
            },
            handler=self._tool_greet,
        )

    async def on_disable(self, ctx: PluginContext) -> None:
        self._ctx = None  # 注册与资源由宿主强制撤销

    async def _handle_hello(self, cmd: CommandContext) -> str:
        assert self._ctx is not None
        greeting = str(self._ctx.config.get("greeting", "你好"))
        actor = cmd.run.actor
        return (
            f"{greeting}，{actor.display_name or actor.user_id}！\n"
            f"会话：{cmd.run.session.as_scope()}\n"
            f"身份：{actor.role.value}（{actor.platform} {actor.user_id}）\n"
            f"插件：{self._ctx.plugin_id} 配置生效中"
        )

    async def _tool_greet(self, params) -> str:
        assert self._ctx is not None
        greeting = str(self._ctx.config.get("greeting", "你好"))
        name = params.get("name", "朋友") if isinstance(params, dict) else "朋友"
        return f"{greeting}，{name}！"

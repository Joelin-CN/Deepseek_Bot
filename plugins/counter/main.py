"""计数插件（内置，M4 示例）。

- ``/counter``：本会话计数 +step，并显示会话计数与全局总计；
- ``/counter reset``：清零当前会话计数（全局总计保留）；
- 计数存放在本插件 KV 命名空间（builtin.counter），跨重启持久；
- on_enable 启动一个只写本地日志的心跳任务，验证后台任务归属与停用
  取消——**绝不主动发送 QQ 消息**。
"""

from __future__ import annotations

import asyncio

from companion.api import CommandContext, Plugin, PluginContext


class CounterPlugin(Plugin):
    def __init__(self) -> None:
        self._ctx: PluginContext | None = None

    async def on_enable(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.register_command(
            "counter",
            self._handle_counter,
            description="会话计数演示（KV 持久化）",
            usage="/counter [reset]",
        )
        interval = float(ctx.config.get("heartbeat_interval", 60.0))
        if interval > 0:
            ctx.create_task(self._heartbeat(interval), name="heartbeat")

    async def on_disable(self, ctx: PluginContext) -> None:
        self._ctx = None  # 心跳任务由宿主取消，注册由宿主撤销

    async def _handle_counter(self, cmd: CommandContext) -> str:
        assert self._ctx is not None
        scope = cmd.run.session.as_scope()
        if cmd.args.strip() == "reset":
            await self._ctx.kv.delete(f"count:{scope}")
            return f"已重置会话计数（{scope}）；全局总计保留。"
        step = max(1, int(self._ctx.config.get("step", 1)))
        session_count = int(await self._ctx.kv.get(f"count:{scope}", 0)) + step
        await self._ctx.kv.set(f"count:{scope}", session_count)
        total = int(await self._ctx.kv.get("total", 0)) + step
        await self._ctx.kv.set("total", total)
        return f"本会话第 {session_count} 次计数，全局总计 {total} 次。"

    async def _heartbeat(self, interval: float) -> None:
        """仅写日志验证后台任务生命周期；不发消息、不访问其他插件数据。"""
        assert self._ctx is not None
        while True:
            await asyncio.sleep(interval)
            total = await self._ctx.kv.get("total", 0)
            self._ctx.logger.info("counter 心跳：全局总计 %d 次计数", total)

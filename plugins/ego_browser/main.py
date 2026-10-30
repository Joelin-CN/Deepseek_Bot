"""网页任务插件（内置，M5B）。

三个工具（均要求 WEB_BROWSE 能力，当前仅主人；v2 处理器带执行上下文
与已校验参数）：

- ``web_search(query)``：经 ego-lite 固定搜索流程检索公开网页；
- ``web_open(url)``：打开公网 HTTP/HTTPS 页面，返回标题、实际 URL、正
  文摘录与链接（跳转目标同样过边界校验）；
- ``web_finish()``：结束并清理本任务的 TaskSpace（正常收尾时模型应调
  用；取消/超时/失败由任务清理钩子兜底）。

边界：只处理公开网页信息任务；不接受任意代码/命令/文件读取；工具结
果是外部数据，不构成指令；并发准入为全局 1（忙时立即失败而非排队）。
"""

from __future__ import annotations

from companion.adapters.ego import EgoBrowser
from companion.api import (
    Capability,
    Plugin,
    PluginContext,
    ToolExecutionContext,
    ToolExecutionResult,
)


def _as_result(data: dict) -> ToolExecutionResult:
    """浏览器结果 → 结构化工具结果：失败给模型明确错误码而不是含混成功。"""
    if data.get("ok"):
        return ToolExecutionResult.success(data)
    return ToolExecutionResult.failure(
        str(data.get("error", "browser_error")), str(data.get("detail", ""))
    )

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索关键词（中文或英文）"},
    },
    "required": ["query"],
}
_OPEN_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "要打开的公网 http(s) 地址"},
    },
    "required": ["url"],
}
_FINISH_SCHEMA = {"type": "object", "properties": {}, "required": []}


class EgoBrowserPlugin(Plugin):
    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._browser: EgoBrowser | None = None
        self._cleanup_attached: set[str] = set()

    async def on_enable(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._browser = EgoBrowser(
            search_url=str(ctx.config.get("search_url", "https://www.bing.com/search?q=")),
            page_load_timeout=float(ctx.config.get("page_load_timeout", 45.0)),
            max_pages_per_task=int(ctx.config.get("max_pages_per_task", 6)),
        )
        self._cleanup_attached = set()
        ctx.register_tool(
            "web_search",
            "搜索公开网页，返回标题、链接与摘要列表",
            _SEARCH_SCHEMA,
            self._web_search,
            required_capability=Capability.WEB_BROWSE,
            wants_context=True,
        )
        ctx.register_tool(
            "web_open",
            "打开公网网页并阅读正文（返回标题、实际地址、正文摘录与主要链接）",
            _OPEN_SCHEMA,
            self._web_open,
            required_capability=Capability.WEB_BROWSE,
            wants_context=True,
        )
        ctx.register_tool(
            "web_finish",
            "结束并清理当前网页任务（得到最终答案或确认无需继续后调用）",
            _FINISH_SCHEMA,
            self._web_finish,
            required_capability=Capability.WEB_BROWSE,
            wants_context=True,
        )
        ctx.logger.info("网页任务插件就绪（仅主人可用，公开网页范围）")

    async def on_disable(self, ctx: PluginContext) -> None:
        if self._browser is not None:
            finished = await self._browser.finish_all()  # 停用时清理全部任务空间
            ctx.logger.info("插件停用，已清理任务空间 %d 个", finished)
        self._browser = None
        self._ctx = None

    def _ensure_cleanup(self, exec_ctx: ToolExecutionContext) -> None:
        """任务首个浏览器操作时登记清理钩子（取消/超时/结束都会触发）。"""
        if self._browser is None:
            raise RuntimeError("插件未启用")
        if exec_ctx.task_id not in self._cleanup_attached:
            self._cleanup_attached.add(exec_ctx.task_id)
            browser, task_id = self._browser, exec_ctx.task_id
            exec_ctx.attach_cleanup(lambda: browser.finish_task(task_id))

    async def _web_search(self, exec_ctx: ToolExecutionContext):
        assert self._browser is not None
        self._ensure_cleanup(exec_ctx)
        return _as_result(await self._browser.search(exec_ctx.task_id, exec_ctx.params["query"]))

    async def _web_open(self, exec_ctx: ToolExecutionContext):
        assert self._browser is not None
        self._ensure_cleanup(exec_ctx)
        return _as_result(await self._browser.open(exec_ctx.task_id, exec_ctx.params["url"]))

    async def _web_finish(self, exec_ctx: ToolExecutionContext):
        assert self._browser is not None
        self._cleanup_attached.discard(exec_ctx.task_id)
        return _as_result(await self._browser.finish_task(exec_ctx.task_id))

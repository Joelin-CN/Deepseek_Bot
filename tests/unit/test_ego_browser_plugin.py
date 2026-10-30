"""网页任务插件：真实插件加载 + 替身浏览器 + 权限与生命周期。"""

from __future__ import annotations

from pathlib import Path

from companion.core.permissions import RunContext, actor_for
from companion.core.sessions.identity import SessionKey
from companion.core.tools import ToolCall, ToolExecutionService
from companion.core.tasks import TaskManager, TaskState
from companion.persistence import Database
from companion.plugin_system import PluginHost

OWNER = 844281600
MEMBER = 10001
SELF = 3666167977
PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "plugins"


class FakeBrowser:
    def __init__(self):
        self.searches: list[tuple] = []
        self.opens: list[tuple] = []
        self.finished: list[str] = []
        self.fail_search = False

    async def search(self, task_key, query):
        self.searches.append((task_key, query))
        if self.fail_search:
            return {"ok": False, "error": "timeout", "detail": "替身失败"}
        return {"ok": True, "results": [{"title": "结果", "url": "https://example.com/", "snippet": "s"}]}

    async def open(self, task_key, url):
        self.opens.append((task_key, url))
        return {"ok": True, "url": url, "title": "页面", "text": "正文", "links": []}

    async def finish_task(self, task_key):
        self.finished.append(task_key)
        return {"ok": True, "finished": True}

    async def finish_all(self):
        count = 2
        self.finished.append("ALL")
        return count


def make_msg(text: str = "查点公开资料"):
    from companion.core.events import InboundMessage

    return InboundMessage(
        message_id=1, session=SessionKey("qq", SELF, "private", OWNER),
        sender_id=OWNER, sender_nickname="测试", sub_type="friend", text=text, time=0,
    )


def ctx_for(user_id: int = OWNER, task_id: str = "fixed-task-id") -> RunContext:
    return RunContext(
        session=SessionKey("qq", SELF, "private", user_id),
        actor=actor_for("qq", user_id, "测试", owner_id=OWNER, allowed_senders={OWNER, MEMBER}),
        task_id=task_id,
    )


async def make_env(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    host = PluginHost(
        roots=[PLUGINS_ROOT],
        db=db,
        tools=host_tools(),
        data_dir=tmp_path,
        instance_settings={"builtin.ego_browser": {"enabled": True, "config": {}}},
    )
    await host.startup()
    record = host._records["builtin.ego_browser"]
    fake = FakeBrowser()
    record.instance._browser = fake  # 替身浏览器：真实插件逻辑 + 假浏览器
    manager = TaskManager()
    service = ToolExecutionService(
        registry=host_tools_reg(host), is_owner_active=host.is_plugin_active,
        cleanup_sink=manager.attach_cleanup,
    )
    service.list_for(ctx_for(OWNER))
    return host, db, fake, service, manager


def host_tools():
    from companion.core.tools import ToolRegistry

    return ToolRegistry()


def host_tools_reg(host):
    return host._tools


async def test_owner_can_use_web_tools_and_cleanup_hook_fires(tmp_path):
    host, db, fake, service, manager = await make_env(tmp_path)
    try:
        visible = [name for name, _ in service.list_for(ctx_for(OWNER))]
        assert set(visible) >= {"web_search", "web_open", "web_finish"}
        manager.create(ctx_for(OWNER, task_id="task-real-1"), make_msg())  # 生产流程由应答器在执行前创建任务记录

        result = await service.execute(
            ToolCall(id="c1", name="web_search", arguments='{"query": "今天新闻"}'),
            ctx_for(OWNER, task_id="task-real-1"), task_id="task-real-1",
        )
        assert result.ok and result.content["results"][0]["title"] == "结果"
        assert fake.searches == [("task-real-1", "今天新闻")]

        result = await service.execute(
            ToolCall(id="c2", name="web_open", arguments='{"url": "https://example.com/a"}'),
            ctx_for(OWNER, task_id="task-real-1"), task_id="task-real-1",
        )
        assert result.ok and result.content["title"] == "页面"

        # 任务结束（任意终态）触发清理钩子 → finish_task
        manager.finish("task-real-1", TaskState.COMPLETED)
        await __import__("asyncio").sleep(0.1)
        assert "task-real-1" in fake.finished
    finally:
        await host.shutdown()
        await db.close()


async def test_member_cannot_use_web_tools(tmp_path):
    host, db, fake, service, manager = await make_env(tmp_path)
    try:
        member_visible = [name for name, _ in service.list_for(ctx_for(MEMBER))]
        assert not any(name.startswith("web_") for name in member_visible)

        service.list_for(ctx_for(OWNER))  # 用主人名单模拟模型拿到旧工具表
        result = await service.execute(
            ToolCall(id="c1", name="web_search", arguments='{"query": "x"}'),
            ctx_for(MEMBER), task_id="t",
        )
        assert result.error == "permission_denied"
        assert fake.searches == []
    finally:
        await host.shutdown()
        await db.close()


async def test_disabled_plugin_blocks_execution_and_finishes_spaces(tmp_path):
    host, db, fake, service, manager = await make_env(tmp_path)
    try:
        manager.create(ctx_for(OWNER, task_id="t0"), make_msg())
        await service.execute(  # 先产生一个任务空间
            ToolCall(id="c0", name="web_search", arguments='{"query": "x"}'),
            ctx_for(OWNER), task_id="t0",
        )
        ok, _ = await host.disable("builtin.ego_browser")
        assert ok
        service.list_for(ctx_for(OWNER))  # 刷新名单
        result = await service.execute(
            ToolCall(id="c1", name="web_search", arguments='{"query": "x"}'),
            ctx_for(OWNER), task_id="t",
        )
        # 停用即撤销注册：旧名单里的工具变成 unknown_tool，同样不会执行
        assert result.error == "unknown_tool"
        assert fake.searches == [("t0", "x")]  # 停用后没有新增浏览器调用
        assert "ALL" in fake.finished  # 停用清理了全部任务空间
    finally:
        await db.close()


async def test_browser_failure_is_structured_not_crash(tmp_path):
    host, db, fake, service, manager = await make_env(tmp_path)
    try:
        manager.create(ctx_for(OWNER, task_id="t"), make_msg())
        fake.fail_search = True
        result = await service.execute(
            ToolCall(id="c1", name="web_search", arguments='{"query": "x"}'),
            ctx_for(OWNER, task_id="t"), task_id="t",
        )
        assert result.ok is False
        assert result.error == "timeout" and "替身失败" in result.content["detail"]  # 结构化失败回传模型
    finally:
        await host.shutdown()
        await db.close()

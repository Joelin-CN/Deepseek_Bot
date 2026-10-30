"""插件宿主：生命周期、派发、隔离、管理命令与状态持久化。"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

from companion.api import HandlerResult
from companion.plugin_system import PluginHost, PluginState

from plugin_harness import MEMBER, OWNER, ctx_for, group_msg, make_host, make_plugin, private_msg

HELLO_BODY = """\
        async def hello(cmd):
            return f"你好 {cmd.args}".strip()
        ctx.register_command("hello", hello, description="问候", usage="/hello 文本")"""


def status_of(host, plugin_id):
    return next(s for s in host.statuses() if s.plugin_id == plugin_id)


# ---- 启动与状态 ----


async def test_startup_loads_but_not_enables_without_config(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        assert status_of(host, "t.alpha").state == PluginState.DISABLED.value
        result = await host.handle_message(ctx_for(private_msg("/hello")), private_msg("/hello"))
        assert result is None  # 未启用不派发
    finally:
        await host.shutdown()
        await db.close()


async def test_config_enabled_starts_enabled(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        assert status_of(host, "t.alpha").state == PluginState.ENABLED.value
        msg = private_msg("/hello 世界", message_id=2)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result is not None and result.consumed
        assert result.text == "你好 世界"
    finally:
        await host.shutdown()
        await db.close()


async def test_load_failure_recorded_in_statuses(tmp_path):
    make_plugin(tmp_path / "plugins", "t.broken", manifest_overrides={"version": "不是版本"})
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        statuses = host.statuses()
        assert len(statuses) == 1
        assert statuses[0].state == PluginState.FAILED.value
        assert "version 非法" in statuses[0].error
    finally:
        await host.shutdown()
        await db.close()


async def test_duplicate_id_across_roots_second_fails(tmp_path):
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    make_plugin(root1, "t.dup", HELLO_BODY)
    make_plugin(root2, "t.dup", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, roots=[root1, root2])
    try:
        await host.startup()
        dup_status = [s for s in host.statuses() if "重复" in (s.error or "")]
        assert len(dup_status) == 1
        assert status_of(host, "t.dup").state == PluginState.DISABLED.value
    finally:
        await host.shutdown()
        await db.close()


async def test_startup_twice_rejected(tmp_path):
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        with pytest.raises(Exception, match="只能调用一次"):
            await host.startup()
    finally:
        await host.shutdown()
        await db.close()


# ---- 启用失败回滚与冲突 ----


async def test_enable_failure_rolls_back_registrations(tmp_path):
    body = HELLO_BODY + "\n        ctx.register_command('rb', hello)\n        raise RuntimeError('启用炸了')"
    make_plugin(tmp_path / "plugins", "t.rb", body)
    host, db, tools = await make_host(tmp_path, instances={"t.rb": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        status = status_of(host, "t.rb")
        assert status.state == PluginState.DISABLED.value
        assert "启用失败" in status.error and "启用炸了" in status.error
        msg = private_msg("/hello", message_id=3)
        assert await host.handle_message(ctx_for(msg), msg) is None  # 注册已回滚
    finally:
        await host.shutdown()
        await db.close()


async def test_command_conflict_second_plugin_fails_first_intact(tmp_path):
    make_plugin(tmp_path / "plugins", "t.a", HELLO_BODY)  # 注册 hello
    make_plugin(tmp_path / "plugins", "t.b", HELLO_BODY)  # 也注册 hello → 冲突
    host, db, tools = await make_host(
        tmp_path,
        instances={"t.a": {"enabled": True, "config": {}}, "t.b": {"enabled": True, "config": {}}},
    )
    try:
        await host.startup()
        assert status_of(host, "t.a").state == PluginState.ENABLED.value
        status_b = status_of(host, "t.b")
        assert status_b.state == PluginState.DISABLED.value
        assert "已由插件 t.a 注册" in status_b.error
        msg = private_msg("/hello", message_id=4)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result.text == "你好"  # t.a 仍正常
    finally:
        await host.shutdown()
        await db.close()


async def test_reserved_command_name_rejected(tmp_path):
    body = "        ctx.register_command('plugin', lambda cmd: None)"
    make_plugin(tmp_path / "plugins", "t.res", body)
    host, db, tools = await make_host(tmp_path, instances={"t.res": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        status = status_of(host, "t.res")
        assert status.state == PluginState.DISABLED.value
        assert "保留" in status.error
    finally:
        await host.shutdown()
        await db.close()


# ---- 派发语义 ----


async def test_unknown_command_and_plain_message_propagate(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        for text in ("/nope", "普通聊天", "/", "/plugins"):  # /plugins 不是管理命令也不是已注册命令
            msg = private_msg(text, message_id=10)
            assert await host.handle_message(ctx_for(msg), msg) is None, text
    finally:
        await host.shutdown()
        await db.close()


async def test_group_messages_not_dispatched(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = group_msg("/hello")
        assert await host.handle_message(ctx_for(msg), msg) is None
    finally:
        await host.shutdown()
        await db.close()


async def test_command_exception_yields_error_reply_not_silence(tmp_path):
    body = """\
        async def boom(cmd):
            raise ValueError("命令炸了")
        ctx.register_command("boom", boom)"""
    make_plugin(tmp_path / "plugins", "t.boom", body)
    host, db, tools = await make_host(tmp_path, instances={"t.boom": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = private_msg("/boom", message_id=11)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result is not None and result.consumed
        assert "t.boom" in result.text and "出错" in result.text  # 可定位且可见
    finally:
        await host.shutdown()
        await db.close()


async def test_command_returning_none_consumes_without_reply(tmp_path):
    body = """\
        async def quiet(cmd):
            return None
        ctx.register_command("quiet", quiet)"""
    make_plugin(tmp_path / "plugins", "t.quiet", body)
    host, db, tools = await make_host(tmp_path, instances={"t.quiet": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = private_msg("/quiet", message_id=12)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result == HandlerResult.consume()
    finally:
        await host.shutdown()
        await db.close()


async def test_message_handler_order_consume_and_exception_isolation(tmp_path):
    body = """\
        box = {}
        async def first(run, message):
            raise RuntimeError("处理器炸了")
        async def second(run, message):
            if message.text == "拦截":
                return HandlerResult.reply("被 second 拦截")
            return None
        ctx.register_message_handler(first, name="first", priority=1)
        ctx.register_message_handler(second, name="second", priority=2)"""
    make_plugin(tmp_path / "plugins", "t.h", body)
    host, db, tools = await make_host(tmp_path, instances={"t.h": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = private_msg("拦截", message_id=13)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result is not None and result.text == "被 second 拦截"  # first 异常被跳过
        msg = private_msg("不拦截", message_id=14)
        assert await host.handle_message(ctx_for(msg), msg) is None
    finally:
        await host.shutdown()
        await db.close()


async def test_handler_bad_return_type_treated_as_error(tmp_path, caplog):
    body = """\
        async def bad(run, message):
            return 12345
        async def cmd(cmd):
            return 67890
        ctx.register_message_handler(bad, name="bad")
        ctx.register_command("badcmd", cmd)"""
    make_plugin(tmp_path / "plugins", "t.bad", body)
    host, db, tools = await make_host(tmp_path, instances={"t.bad": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = private_msg("普通", message_id=15)
        with caplog.at_level(logging.ERROR):
            result = await host.handle_message(ctx_for(msg), msg)  # 消息处理器返回 123 → 记日志并跳过
        assert result is None
        msg = private_msg("/badcmd", message_id=16)
        result = await host.handle_message(ctx_for(msg), msg)
        assert "出错" in result.text  # 命令返回 123 → 错误回复
        assert any("异常" in r.message for r in caplog.records)
    finally:
        await host.shutdown()
        await db.close()


# ---- 工具登记与撤销 ----


async def test_tool_registered_and_revoked_on_disable(tmp_path):
    body = """\
        async def tool(params):
            return "ok"
        ctx.register_tool("demo_greet", "演示工具", {"type": "object", "properties": {"x": {"type": "string"}}}, tool)"""
    make_plugin(tmp_path / "plugins", "t.tool", body)
    host, db, tools = await make_host(tmp_path, instances={"t.tool": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        descriptor = tools.get("demo_greet")
        assert descriptor is not None and descriptor.owner == "t.tool"
        assert (await tools.executor("demo_greet")({})) == "ok"
        await host.disable("t.tool")
        assert tools.get("demo_greet") is None
        assert tools.list_tools(owner="t.tool") == []
    finally:
        await host.shutdown()
        await db.close()


# ---- 后台任务 ----


async def test_background_task_cancelled_on_disable_and_crash_observed(tmp_path, caplog):
    body = """\
        import asyncio
        async def forever():
            await asyncio.sleep(3600)
        ctx.create_task(forever(), name="hb")"""
    crash_body = """\
        import asyncio
        async def crash():
            await asyncio.sleep(0.01)
            raise RuntimeError("任务炸了")
        ctx.create_task(crash(), name="crash")"""
    make_plugin(tmp_path / "plugins", "t.task", body)
    make_plugin(tmp_path / "plugins", "t.crash", crash_body)
    host, db, tools = await make_host(tmp_path, instances={"t.task": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        assert host._tasks.count("t.task") == 1
        await host.disable("t.task")
        assert host._tasks.count("t.task") == 0  # 停用即取消

        with caplog.at_level(logging.ERROR, logger="companion.plugin.t.crash"):
            ok, _ = await host.enable("t.crash")
            assert ok
            await asyncio.sleep(0.08)
        assert any("后台任务异常退出" in r.message for r in caplog.records)
        assert host._tasks.count("t.crash") == 0  # 崩溃任务被移出跟踪
    finally:
        await host.shutdown()
        await db.close()


# ---- 管理命令 ----


async def _owner(host, text, message_id=20):
    msg = private_msg(text, message_id=message_id)
    return await host.handle_message(ctx_for(msg), msg)


async def _member(host, text, message_id=21):
    msg = private_msg(text, message_id=message_id, user_id=MEMBER)
    return await host.handle_message(ctx_for(msg, owner_id=OWNER), msg)


async def test_management_owner_flow(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        result = await _owner(host, "/plugin list")
        assert "t.alpha" in result.text and "DISABLED" in result.text
        result = await _owner(host, "/plugin enable t.alpha")
        assert "启用成功" in result.text
        assert status_of(host, "t.alpha").state == PluginState.ENABLED.value
        result = await _owner(host, "/plugin status t.alpha")
        assert "命令 1 个" in result.text
        result = await _owner(host, "/hello 管理", message_id=22)
        assert result.text == "你好 管理"
        result = await _owner(host, "/plugin disable t.alpha")
        assert "停用成功" in result.text
        msg = private_msg("/hello", message_id=23)
        assert await host.handle_message(ctx_for(msg), msg) is None  # 停用后命令撤销
        result = await _owner(host, "/plugin enable t.nope")
        assert "启用失败" in result.text and "未发现插件" in result.text
    finally:
        await host.shutdown()
        await db.close()


async def test_management_denied_for_member(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        for text in ("/plugin list", "/plugin enable t.alpha", "/plugin disable t.alpha"):
            result = await _member(host, text)
            assert "仅限主人" in result.text, text
        assert status_of(host, "t.alpha").state == PluginState.DISABLED.value
    finally:
        await host.shutdown()
        await db.close()


async def test_repeat_enable_disable_do_not_duplicate(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path)
    try:
        await host.startup()
        for _ in range(3):
            ok, msg = await host.enable("t.alpha")
            assert ok
            msg = private_msg("/hello", message_id=30)
            result = await host.handle_message(ctx_for(msg), msg)
            assert result.text == "你好"
            ok, _ = await host.disable("t.alpha")
            assert ok
            ok2, msg2 = await host.disable("t.alpha")
            assert ok2 and "已处于停用" in msg2
        ok, msg3 = await host.enable("t.alpha")
        assert ok and "已启用" in msg3
        ok2, msg4 = await host.enable("t.alpha")
        assert ok2 and "已处于启用" in msg4
        # 重复启停后注册表只有一条命令
        assert host._commands.get("hello") is not None
        assert len([e for e in host._commands.entries() if e.name == "hello"]) == 1
    finally:
        await host.shutdown()
        await db.close()


# ---- 配置校验 ----


async def test_config_unknown_key_and_wrong_type_fail_enable(tmp_path):
    make_plugin(tmp_path / "plugins", "t.cfg", HELLO_BODY, manifest_overrides={
        "config": {"greeting": {"type": "string", "default": "默认问候"}}})
    host, db, tools = await make_host(
        tmp_path, instances={"t.cfg": {"enabled": True, "config": {"greeting": 123}}})
    try:
        await host.startup()
        status = status_of(host, "t.cfg")
        assert status.state == PluginState.DISABLED.value
        assert "要求类型 string" in status.error
    finally:
        await host.shutdown()
        await db.close()

    host2, db2, _ = await make_host(
        tmp_path, instances={"t.cfg": {"enabled": True, "config": {"unknown_key": "x"}}})
    try:
        await host2.startup()
        status = status_of(host2, "t.cfg")
        assert "未声明配置项" in status.error
    finally:
        await host2.shutdown()
        await db2.close()


async def test_config_defaults_and_overrides_visible(tmp_path):
    body = """\
        box = {}
        async def show(cmd):
            return str(box["cfg"])
        box["cfg"] = ctx.config.get("greeting", "<无>")
        ctx.register_command("show", show)"""
    make_plugin(tmp_path / "plugins", "t.cfg", body, manifest_overrides={
        "config": {"greeting": {"type": "string", "default": "默认问候"}}})
    host, db, tools = await make_host(tmp_path, instances={"t.cfg": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        msg = private_msg("/show", message_id=40)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result.text == "默认问候"
        await host.disable("t.cfg")
        host._instance_settings["t.cfg"]["config"]["greeting"] = "覆盖问候"
        ok, _ = await host.enable("t.cfg")
        assert ok
        msg = private_msg("/show", message_id=41)
        result = await host.handle_message(ctx_for(msg), msg)
        assert result.text == "覆盖问候"
    finally:
        await host.shutdown()
        await db.close()


# ---- 数据目录与 KV 隔离 ----


async def test_data_dir_created_under_plugin_namespace(tmp_path):
    make_plugin(tmp_path / "plugins", "t.data", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.data": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        expected = (tmp_path / "plugins" / "data" / "t.data").resolve()
        assert expected.is_dir()
        record = host._records["t.data"]
        assert record.context.data_dir == expected
    finally:
        await host.shutdown()
        await db.close()


async def test_kv_namespaces_isolated_between_plugins(tmp_path):
    body = """\
        tag = ctx.plugin_id.replace(".", "_")
        async def put(cmd):
            await ctx.kv.set("shared", ctx.plugin_id)
        async def get(cmd):
            return str(await ctx.kv.get("shared"))
        ctx.register_command("put_" + tag, put)
        ctx.register_command("get_" + tag, get)"""
    make_plugin(tmp_path / "plugins", "t.pa", body)
    make_plugin(tmp_path / "plugins", "t.pb", body)
    host, db, tools = await make_host(
        tmp_path, instances={"t.pa": {"enabled": True, "config": {}}, "t.pb": {"enabled": True, "config": {}}})
    try:
        await host.startup()
        for pid in ("t.pa", "t.pb"):
            msg = private_msg(f"/put_{pid.replace('.', '_')}", message_id=50)
            result = await host.handle_message(ctx_for(msg), msg)
            assert result is not None, pid
        for pid in ("t.pa", "t.pb"):
            msg = private_msg(f"/get_{pid.replace('.', '_')}", message_id=51)
            result = await host.handle_message(ctx_for(msg), msg)
            assert result.text == pid  # 各自命名空间互不可见
    finally:
        await host.shutdown()
        await db.close()


# ---- 运行状态持久化 ----


async def test_runtime_state_overrides_config_on_restart(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": False, "config": {}}})
    try:
        await host.startup()
        result = await _owner(host, "/plugin enable t.alpha")  # 写运行状态 True
        assert "启用成功" in result.text
        await host.shutdown()  # shutdown 不改写运行状态
    finally:
        pass

    host2, db2, _ = await make_host(tmp_path, instances={"t.alpha": {"enabled": False, "config": {}}})
    try:
        await host2.startup()
        status = status_of(host2, "t.alpha")
        assert status.state == PluginState.ENABLED.value  # 运行状态行优先
        assert status.enabled_source == "runtime"
    finally:
        await host2.shutdown()
        await db2.close()


async def test_shutdown_disable_does_not_persist(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    await host.startup()
    await host.shutdown()

    host2, db2, _ = await make_host(tmp_path, instances={"t.alpha": {"enabled": False, "config": {}}})
    try:
        await host2.startup()
        assert status_of(host2, "t.alpha").state == PluginState.DISABLED.value
        assert status_of(host2, "t.alpha").enabled_source == "default"
    finally:
        await host2.shutdown()
        await db2.close()
    await db.close()


async def test_disable_of_disabled_persists_intent(tmp_path):
    make_plugin(tmp_path / "plugins", "t.alpha", HELLO_BODY)
    host, db, tools = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    await host.startup()
    await host.shutdown()

    host2, db2, _ = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    try:
        await host2.startup()
        result = await _owner(host2, "/plugin disable t.alpha", message_id=60)  # 已启用 → 停用
        assert "停用成功" in result.text
        await host2.shutdown()
    finally:
        pass

    host3, db3, _ = await make_host(tmp_path, instances={"t.alpha": {"enabled": True, "config": {}}})
    try:
        await host3.startup()
        assert status_of(host3, "t.alpha").state == PluginState.DISABLED.value
    finally:
        await host3.shutdown()
        await db3.close()
    await db2.close()
    await db.close()

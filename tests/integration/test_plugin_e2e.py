"""插件系统端到端：完整 BotRunner + StubOneBot + 真实内置插件。

覆盖：内置 /hello 与 /counter 命令闭环、命令回执不入对话历史、普通聊
天继续走应答器、主人管理命令启停、非主人被拒、运行状态与 KV 跨重启
持久化。
"""

from __future__ import annotations

from companion.config.settings import PluginInstanceSettings

from test_onebot_stub import (
    ALLOWED,
    FIXED_REPLY,
    StubOneBot,
    make_db,
    make_settings,
    private_event,
    running_bot,
    wait_for_status,
    wait_workers,
)

MEMBER_QQ = 10002
HELLO_SCOPE = f"qq/3666167977/private/{ALLOWED}"


def text_event(message_id: int, text: str, user_id: int = ALLOWED) -> dict:
    return private_event(
        message_id=message_id,
        user_id=user_id,
        message=[{"type": "text", "data": {"text": text}}],
    )


def plugin_settings(port: int, tmp_path=None, **bot_overrides):
    settings = make_settings(port, **bot_overrides)
    if tmp_path is not None:
        settings.data_dir_resolved = tmp_path  # 插件数据目录随测试走，不落仓库 data/
    settings.plugins.instances["builtin.hello"] = PluginInstanceSettings(enabled=True)
    settings.plugins.instances["builtin.counter"] = PluginInstanceSettings(
        enabled=True, config={"heartbeat_interval": 0.0}
    )
    return settings


def _reply_text(action: dict) -> str:
    return action["params"]["message"][0]["data"]["text"]


async def test_builtin_plugins_end_to_end(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    try:
        async with running_bot(plugin_settings(stub.port, tmp_path), db) as (runner1, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner1)

            await stub.push_event(text_event(1001, "/hello"))
            hello_reply = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert hello_reply.startswith("你好")
            assert HELLO_SCOPE in hello_reply  # 来自运行上下文的会话信息

            await stub.push_event(text_event(1002, "/counter"))
            count1 = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=2))
            assert "本会话第 1 次计数" in count1 and "全局总计 1 次" in count1

            await stub.push_event(text_event(1003, "/counter"))
            count2 = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=3))
            assert "本会话第 2 次计数" in count2

            await stub.push_event(text_event(1004, "/counter reset"))
            reset = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=4))
            assert "已重置会话计数" in reset

            # 未被插件消费的普通消息继续走原应答链路
            await stub.push_event(text_event(1005, "普通聊天"))
            plain = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=5))
            assert plain == FIXED_REPLY

            for mid in (1001, 1002, 1003, 1004, 1005):
                await wait_for_status(db, mid, "sent_ok")

        rows = await db.fetchall(
            "SELECT platform_message_id, status, reply_content FROM messages ORDER BY id"
        )
        assert all(status == "sent_ok" for _, status, _ in rows)
        by_id = {mid: (status, reply) for mid, status, reply in rows}
        assert by_id[1001][1] == ""  # 命令回执不进入对话历史
        assert by_id[1005][1] == FIXED_REPLY  # 普通回复保留全文
        kv_total = await db.fetchone(
            "SELECT value FROM plugin_kv WHERE namespace='builtin.counter' AND key='total'"
        )
        assert kv_total is not None and kv_total[0] == "2"
    finally:
        await db.close()
        await stub.stop()


async def test_plugin_management_owner_and_member(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    settings = plugin_settings(stub.port, tmp_path, allowed_private_senders=[ALLOWED, MEMBER_QQ])
    try:
        async with running_bot(settings, db) as (runner1, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner1)

            await stub.push_event(text_event(1101, "/plugin list"))
            listing = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert "builtin.hello" in listing and "builtin.counter" in listing

            # 非主人（白名单成员）管理被拒
            await stub.push_event(text_event(1102, "/plugin list", user_id=MEMBER_QQ))
            denied = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=2))
            assert "仅限主人" in denied

            # 主人停用 counter：命令消失，回落到固定应答
            await stub.push_event(text_event(1103, "/plugin disable builtin.counter"))
            disabled = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=3))
            assert "停用成功" in disabled
            await stub.push_event(text_event(1104, "/counter"))
            fallback = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=4))
            assert fallback == FIXED_REPLY

            # 重新启用：命令恢复，计数从 KV 继续
            await stub.push_event(text_event(1105, "/plugin enable builtin.counter"))
            enabled = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=5))
            assert "启用成功" in enabled
            await stub.push_event(text_event(1106, "/counter"))
            resumed = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=6))
            assert "本会话第 1 次计数" in resumed  # 全新会话进程内 KV 保留全局总计
            assert "全局总计 1 次" in resumed
    finally:
        await db.close()
        await stub.stop()


async def test_runtime_state_and_kv_survive_restart(tmp_path):
    stub = StubOneBot()
    await stub.start()
    db = await make_db(tmp_path)
    settings = plugin_settings(stub.port, tmp_path)
    try:
        async with running_bot(settings, db) as (runner1, task):
            await stub.wait_for_action("get_login_info")
            await wait_workers(runner1)
            await stub.push_event(text_event(1201, "/counter"))
            first = _reply_text(await stub.wait_for_action("send_private_msg"))
            assert "全局总计 1 次" in first
            await stub.push_event(text_event(1202, "/plugin disable builtin.hello"))
            disabled = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=2))
            assert "停用成功" in disabled

        # 重启：counter 按配置仍启用且 KV 计数保留；hello 受运行状态行约束保持停用
        settings2 = plugin_settings(stub.port, tmp_path)
        async with running_bot(settings2, db) as (runner2, task):
            await stub.wait_for_action("get_login_info", occurrence=2)
            await wait_workers(runner2)
            await stub.push_event(text_event(1203, "/counter"))
            resumed = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=3))
            assert "全局总计 2 次" in resumed
            await stub.push_event(text_event(1204, "/hello"))
            fallback = _reply_text(await stub.wait_for_action("send_private_msg", occurrence=4))
            assert fallback == FIXED_REPLY  # hello 仍停用 → 回落普通应答

            for mid in (1203, 1204):
                await wait_for_status(db, mid, "sent_ok")

        state_row = await db.fetchone(
            "SELECT enabled FROM plugin_runtime_state WHERE plugin_id='builtin.hello'"
        )
        assert state_row is not None and state_row[0] == 0
    finally:
        await db.close()
        await stub.stop()

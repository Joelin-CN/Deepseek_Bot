"""显式记忆命令路由：记住 / 查看 / 搜索 / 修改 / 忘记 / 权限拒绝。"""

import re

from companion.core.events import InboundMessage, SessionKey
from companion.core.permissions import Role, RunContext, actor_for
from companion.core.pipeline import MemoryCommandRouter
from companion.memory import MemoryService
from companion.persistence import Database

SELF_ID = 3666167977
OWNER = 844281600
STRANGER = 999999999


def _message(text: str, *, user_id: int = OWNER, chat_type: str = "private") -> InboundMessage:
    target = 4321 if chat_type == "group" else user_id
    return InboundMessage(
        message_id=555,
        session=SessionKey("qq", SELF_ID, chat_type, target),
        sender_id=user_id,
        sender_nickname="Zero",
        sub_type="friend" if chat_type == "private" else "normal",
        text=text,
        time=1793182844,
    )


def _ctx(user_id: int = OWNER, *, chat_type: str = "private") -> RunContext:
    target = 4321 if chat_type == "group" else user_id
    session = SessionKey("qq", SELF_ID, chat_type, target)
    actor = actor_for("qq", user_id, "Zero", owner_id=OWNER, allowed_senders={OWNER})
    return RunContext(session=session, actor=actor)


async def _router(tmp_path) -> tuple[MemoryCommandRouter, Database]:
    db = await Database.open(tmp_path / "bot.db")
    return MemoryCommandRouter(MemoryService.from_database(db)), db


async def test_remember_command_variants(tmp_path):
    router, db = await _router(tmp_path)
    try:
        cases = [
            ("记住：我喜欢蓝色", "我喜欢蓝色"),
            ("记住:我也用冒号", "我也用冒号"),
            ("记住 我用空格", "我用空格"),
        ]
        for text, content in cases:
            reply = await router.try_handle(_ctx(), _message(text))
            assert reply is not None and reply.startswith("已记住（#"), reply
            assert content in reply
    finally:
        await db.close()


async def test_remember_empty_content_reports_usage_hint(tmp_path):
    router, db = await _router(tmp_path)
    try:
        reply = await router.try_handle(_ctx(), _message("记住："))
        assert reply is not None and "为空" in reply
    finally:
        await db.close()


async def test_view_search_revise_forget_flow(tmp_path):
    router, db = await _router(tmp_path)
    try:
        ctx = _ctx()
        reply = await router.try_handle(ctx, _message("记住：服务器续费在 12 月"))
        assert reply is not None
        mid = int(re.search(r"#(\d+)", reply).group(1))

        view = await router.try_handle(ctx, _message("查看记忆"))
        assert f"#{mid}" in view and "服务器续费" in view

        search = await router.try_handle(ctx, _message("搜索记忆 续费"))
        assert f"#{mid}" in search

        revised = await router.try_handle(ctx, _message(f"修改记忆 #{mid}：续费提前到 11 月"))
        assert "已修改" in revised and "11 月" in revised
        new_mid = int(re.findall(r"#(\d+)", revised)[-1])  # 修订产生的新编号
        assert "短期对话上下文已清空" in revised

        # 旧编号已 superseded，不能再改/删；有效的是新编号
        assert "没有找到" in await router.try_handle(ctx, _message(f"忘记 #{mid}"))

        forgot = await router.try_handle(ctx, _message(f"忘记 #{new_mid}"))
        assert forgot is not None and forgot.startswith(f"已忘记 #{new_mid}。")
        assert "短期对话上下文已一并清空" in forgot
        view2 = await router.try_handle(ctx, _message("查看记忆"))
        assert "当前没有记住的内容" in view2

        missing = await router.try_handle(ctx, _message(f"忘记 #{new_mid}"))
        assert "没有找到" in missing
    finally:
        await db.close()


async def test_non_command_text_returns_none(tmp_path):
    router, db = await _router(tmp_path)
    try:
        assert await router.try_handle(_ctx(), _message("今天天气怎么样")) is None
        assert await router.try_handle(_ctx(), _message("")) is None
        assert await router.try_handle(_ctx(), _message("记")) is None
    finally:
        await db.close()


async def test_group_session_commands_ignored(tmp_path):
    router, db = await _router(tmp_path)
    try:
        # 群聊里同样的话不作为记忆命令处理（M6 之前群消息在筛选层已拒绝，这里防御性验证）
        reply = await router.try_handle(_ctx(chat_type="group"), _message("记住：群里的", chat_type="group"))
        assert reply is None
    finally:
        await db.close()


async def test_stranger_gets_permission_denied_reply(tmp_path):
    router, db = await _router(tmp_path)
    try:
        ctx = _ctx(STRANGER)
        assert ctx.actor.role is Role.UNKNOWN
        reply = await router.try_handle(ctx, _message("记住：我是谁"))
        assert reply is not None and "权限" in reply
    finally:
        await db.close()


async def test_forget_and_revise_trigger_on_change_callback(tmp_path):
    """忘记/修订成功后回调清空该会话短期上下文；失败不回调。"""
    router, changed, db = await _router_with_on_change(tmp_path)
    try:
        ctx = _ctx()
        reply = await router.try_handle(ctx, _message("记住：临时记忆"))
        mid = int(re.search(r"#(\d+)", reply).group(1))

        assert changed == []  # 记住不清空上下文
        await router.try_handle(ctx, _message(f"忘记 #{mid}"))
        assert changed == [ctx.session]

        changed.clear()
        await router.try_handle(ctx, _message("忘记 9999"))  # 不存在 → 不回调
        assert changed == []

        reply = await router.try_handle(ctx, _message("记住：再记一条"))
        mid2 = int(re.search(r"#(\d+)", reply).group(1))
        await router.try_handle(ctx, _message(f"修改记忆 #{mid2}：改一下"))
        assert changed == [ctx.session]
    finally:
        await db.close()


async def _router_with_on_change(tmp_path):
    from companion.memory import MemoryService
    from companion.persistence import Database

    db = await Database.open(tmp_path / "bot.db")
    changed: list = []
    router = MemoryCommandRouter(
        MemoryService.from_database(db), on_change=changed.append
    )
    return router, changed, db

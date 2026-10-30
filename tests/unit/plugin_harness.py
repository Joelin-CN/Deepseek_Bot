"""插件系统单测共用工具：临时插件目录生成、宿主构建与消息构造。"""

from __future__ import annotations

import json
from pathlib import Path

from companion.core.events import InboundMessage
from companion.core.permissions import RunContext, actor_for
from companion.core.sessions.identity import SessionKey
from companion.core.tools import ToolRegistry
from companion.persistence import Database
from companion.plugin_system import PluginHost

SELF_ID = 3666167977
OWNER = 844281600
MEMBER = 10001

BASE_SOURCE = """\
from companion.api import HandlerResult, Plugin, PluginContext


class TestPlugin(Plugin):
    async def on_enable(self, ctx: PluginContext) -> None:
{body}
"""


def make_plugin(
    root: Path,
    plugin_id: str,
    body: str = "        pass",
    *,
    manifest_overrides: dict | None = None,
    module: str = "main",
    entry_class: str = "TestPlugin",
    manifest_raw: dict | None = None,
) -> Path:
    """在 root 下生成一个插件目录；body 是 on_enable 的缩进代码体。"""
    directory = root / plugin_id.replace(".", "_")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": f"插件{plugin_id}",
        "version": "1.0.0",
        "api_version": "1.0",
        "entry": f"{module}:{entry_class}",
        "description": "测试插件",
    }
    if manifest_raw is not None:
        manifest = manifest_raw
    elif manifest_overrides:
        manifest.update(manifest_overrides)
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / f"{module}.py").write_text(
        BASE_SOURCE.format(body=body), encoding="utf-8"
    )
    return directory


async def make_host(
    tmp_path: Path,
    instances: dict | None = None,
    roots: list[Path] | None = None,
) -> tuple[PluginHost, Database, ToolRegistry]:
    db = await Database.open(tmp_path / "bot.db")
    tools = ToolRegistry()
    host = PluginHost(
        roots=roots or [tmp_path / "plugins"],
        db=db,
        tools=tools,
        data_dir=tmp_path,
        instance_settings=instances or {},
    )
    return host, db, tools


def private_msg(text: str, message_id: int = 1, user_id: int = OWNER) -> InboundMessage:
    session = SessionKey("qq", SELF_ID, "private", user_id)
    return InboundMessage(
        message_id=message_id,
        session=session,
        sender_id=user_id,
        sender_nickname="测试用户",
        sub_type="friend",
        text=text,
        time=1793182844,
    )


def group_msg(text: str, message_id: int = 1, user_id: int = OWNER) -> InboundMessage:
    session = SessionKey("qq", SELF_ID, "group", 987654321)
    return InboundMessage(
        message_id=message_id,
        session=session,
        sender_id=user_id,
        sender_nickname="测试用户",
        sub_type="normal",
        text=text,
        time=1793182844,
    )


def ctx_for(message: InboundMessage, *, owner_id: int = OWNER) -> RunContext:
    actor = actor_for(
        message.session.platform,
        message.sender_id,
        message.sender_nickname,
        owner_id=owner_id,
        allowed_senders={OWNER, MEMBER},
    )
    return RunContext(session=message.session, actor=actor)

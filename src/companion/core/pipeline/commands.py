"""显式记忆命令路由（第一版：不自动提取聊天内容）。

仅私聊生效；写操作需要 MEMORY_MANAGE 能力，查看需要 MEMORY_READ。
命中命令时直接返回回复文本，不再调用模型。命令语法：

- ``记住：内容``（也接受 ``记住:内容`` / ``记住 内容``）
- ``查看记忆`` / ``查看记忆 2``（第 2 页）
- ``搜索记忆 关键词``
- ``修改记忆 #3：新内容``
- ``忘记 #3``
"""

from __future__ import annotations

import logging
import re

from companion.core.events import InboundMessage
from companion.core.permissions import Capability, PermissionDenied, RunContext
from companion.memory.service import MemoryService

logger = logging.getLogger(__name__)

_FORGET_RE = re.compile(r"^忘记\s*#?(\d+)$")
_REVISE_RE = re.compile(r"^修改记忆\s*#?(\d+)\s*[：:]\s*(.+)$")
_VIEW_RE = re.compile(r"^查看记忆(?:\s+(\d+))?$")


class MemoryCommandRouter:
    def __init__(self, service: MemoryService):
        self._service = service

    async def try_handle(self, ctx: RunContext, message: InboundMessage) -> str | None:
        """未命中命令返回 None（继续正常对话路径）。"""
        if message.session.chat_type != "private":
            return None
        text = message.text.strip()
        if not text:
            return None
        try:
            if text.startswith(("记住：", "记住:", "记住 ")):
                return await self._remember(ctx, message, text)
            match = _FORGET_RE.match(text)
            if match:
                return await self._forget(ctx, int(match.group(1)))
            match = _REVISE_RE.match(text)
            if match:
                return await self._revise(ctx, int(match.group(1)), match.group(2).strip())
            match = _VIEW_RE.match(text)
            if match or text == "记忆列表":
                page = int(match.group(1)) if match and match.group(1) else 1
                return await self._list(ctx, page)
            if text.startswith(("搜索记忆 ", "搜索记忆：", "搜索记忆:")):
                for prefix in ("搜索记忆：", "搜索记忆:", "搜索记忆 "):
                    if text.startswith(prefix):
                        return await self._search(ctx, text[len(prefix):].strip())
        except PermissionDenied as exc:
            logger.info("记忆命令被拒绝: %s", exc)
            return "这条命令需要相应权限，当前身份无法执行。"
        return None

    async def _remember(self, ctx: RunContext, message: InboundMessage, text: str) -> str:
        content = ""
        for prefix in ("记住：", "记住:", "记住 "):
            if text.startswith(prefix):
                content = text[len(prefix):].strip()
                break
        if not content:
            return "要记住的内容为空。请使用「记住：内容」。"
        memory_id = await self._service.remember(ctx, content, source_message_id=message.message_id)
        return f"已记住（#{memory_id}）：{content[:80]}"

    async def _forget(self, ctx: RunContext, memory_id: int) -> str:
        ok = await self._service.forget(ctx, memory_id)
        return f"已忘记 #{memory_id}。" if ok else f"没有找到 #{memory_id} 这条有效记忆。"

    async def _revise(self, ctx: RunContext, memory_id: int, new_content: str) -> str:
        if not new_content:
            return "新内容为空。请使用「修改记忆 #编号：新内容」。"
        record = await self._service.revise(ctx, memory_id, new_content)
        if record is None:
            return f"没有找到 #{memory_id} 这条有效记忆。"
        return f"已修改 #{memory_id} → #{record.id}：{new_content[:80]}"

    async def _list(self, ctx: RunContext, page: int) -> str:
        records, total = await self._service.list_memories(ctx, page)
        if not records:
            return "当前没有记住的内容。" if page == 1 else f"第 {page} 页没有内容。"
        lines = [f"#{r.id} {r.preview}" for r in records]
        header = f"记忆（第 {page} 页，共 {total} 条）："
        return header + "\n" + "\n".join(lines)

    async def _search(self, ctx: RunContext, query: str) -> str:
        if not query:
            return "请使用「搜索记忆 关键词」。"
        records, total = await self._service.search(ctx, query)
        if not records:
            return f"没有找到包含「{query}」的记忆。"
        lines = [f"#{r.id} {r.preview}" for r in records]
        return f"找到 {total} 条包含「{query}」的记忆：\n" + "\n".join(lines)

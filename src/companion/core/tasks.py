"""运行中任务的状态管理与取消。

一个"任务"= 一次带工具循环的消息处理（M5 起网页任务等）。任务记录
归属（会话/发起者）、状态、时间、模型轮次与用量；取消通过创建任务时
的 RunContext.cancel 事件传导到应答循环与工具执行，而不是只改状态位。

控制命令（/task …）由 runner 在**会话锁之外**处理，保证长任务持有锁
时主人仍能取消。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

from companion.core.events import InboundMessage
from companion.core.permissions import RunContext

logger = logging.getLogger(__name__)

CleanupFn = Callable[[], Awaitable[None]]
_CLEANUP_TIMEOUT = 20.0


class TaskState(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


@dataclass
class TaskRecord:
    task_id: str
    session: str  # as_scope()
    actor_id: int
    preview: str  # 入站消息预览（与 messages 表同粒度，不落全文）
    state: TaskState = TaskState.RUNNING
    started_ts: float = field(default_factory=time.time)
    finished_ts: float | None = None
    model_rounds: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    note: str = ""  # 终态原因补充（如达到哪种上限）
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _cleanups: list[CleanupFn] = field(default_factory=list)

    def add_round(self, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
        self.model_rounds += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens

    def one_line(self) -> str:
        elapsed = (self.finished_ts or time.time()) - self.started_ts
        return (
            f"{self.task_id[:8]} [{self.state.value}] {self.session} 发起者={self.actor_id} "
            f"耗时={elapsed:.0f}s 轮次={self.model_rounds} 工具={self.tool_calls} "
            f"tokens={self.total_tokens} {self.preview[:30]}"
        )


class TaskManager:
    def __init__(self, history_limit: int = 50):
        self._running: dict[str, TaskRecord] = {}
        self._history: deque[TaskRecord] = deque(maxlen=history_limit)

    def create(self, ctx: RunContext, message: InboundMessage) -> TaskRecord:
        record = TaskRecord(
            task_id=ctx.task_id,
            session=message.session.as_scope(),
            actor_id=message.sender_id,
            preview=message.text[:120],
            cancel=ctx.cancel,
        )
        self._running[record.task_id] = record
        return record

    def attach_cleanup(self, task_id: str, fn: CleanupFn) -> None:
        """任务进入终态时执行清理（浏览器资源等）；由工具执行上下文登记。"""
        record = self._running.get(task_id)
        if record is not None:
            record._cleanups.append(fn)

    def finish(self, task_id: str, state: TaskState | str, note: str = "") -> None:
        record = self._running.pop(task_id, None)
        if record is None:
            return
        if isinstance(state, str):  # 防御：允许字符串形式的状态
            state = TaskState(state)
        record.state = state
        record.finished_ts = time.time()
        record.note = note
        self._history.append(record)
        logger.info(
            "任务结束: %s state=%s note=%s", task_id[:8], state.value, note or "-"
        )
        if record._cleanups:
            async def _run_cleanups() -> None:
                for fn in record._cleanups:
                    try:
                        await asyncio.wait_for(fn(), timeout=_CLEANUP_TIMEOUT)
                    except Exception:
                        logger.exception("任务清理钩子失败: task=%s", task_id[:8])

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:  # 无事件循环（防御）：同步放弃清理并记录
                logger.error("任务清理无事件循环可用: task=%s", task_id[:8])
                return
            loop.create_task(_run_cleanups(), name=f"task-cleanup-{task_id[:8]}")

    def cancel(self, task_id: str, ctx: RunContext) -> tuple[bool, str]:
        """请求取消任务；仅任务发起者本人可取消。"""
        record = self._running.get(task_id)
        if record is None:
            # 允许输入短 ID
            matches = [r for tid, r in self._running.items() if tid.startswith(task_id)]
            if len(matches) == 1:
                record = matches[0]
            elif len(matches) > 1:
                return False, "该前缀匹配多个任务，请使用完整任务 ID"
            else:
                return False, "未找到该运行中的任务"
        if record.actor_id != ctx.actor.user_id:
            return False, "只能取消自己发起的任务"
        record.cancel.set()
        return True, f"已请求取消任务 {record.task_id[:8]}"

    def get(self, task_id: str) -> TaskRecord | None:
        return self._running.get(task_id) or next(
            (r for r in self._history if r.task_id == task_id), None
        )

    def list_tasks(self) -> list[TaskRecord]:
        return list(self._running.values()) + list(self._history)

    def has_running(self) -> bool:
        return bool(self._running)

    async def cancel_all(self, note: str = "进程退出") -> None:
        """退出时请求取消全部运行中任务并等待其结束清理。"""
        records = list(self._running.values())
        for record in records:
            record.cancel.set()
        if records:
            deadline = time.time() + _CLEANUP_TIMEOUT
            while self._running and time.time() < deadline:
                await asyncio.sleep(0.05)
            for leftover in list(self._running.values()):
                self.finish(leftover.task_id, TaskState.CANCELLED, note=note)

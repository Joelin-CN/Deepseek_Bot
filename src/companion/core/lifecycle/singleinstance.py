"""单实例保护。

锁文件记录 PID 与进程创建时间；启动时核验持锁进程是否仍然存活且
创建时间一致（防止 PID 复用误判）。覆盖所有启动入口——直接
``python -m companion``、前台脚本与后台脚本最终都走 ``__main__``。
Windows 下通过 ctypes 查询进程；无法核验创建时间时保守拒绝启动。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class InstanceAlreadyRunning(Exception):
    def __init__(self, pid: int):
        super().__init__(f"已有机器人实例正在运行（PID {pid}）")
        self.pid = pid


if os.name == "nt":
    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.c_uint32),
            ("dwHighDateTime", ctypes.c_uint32),
        ]

    _kernel32 = ctypes.windll.kernel32
    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_TIMEOUT = 0x102
    _ERROR_ACCESS_DENIED = 5


def _process_identity(pid: int) -> tuple[bool, int | None]:
    """返回 (进程是否存活, 创建时间 ticks)；创建时间不可得时为 None。"""
    if os.name != "nt":  # 非 Windows 开发环境退化为存活检查
        try:
            os.kill(pid, 0)
        except OSError:
            return False, None
        return True, None

    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        error = _kernel32.GetLastError()
        return error == _ERROR_ACCESS_DENIED, None  # 拒绝访问按存活处理（保守）
    try:
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel_time = _FILETIME()
        user_time = _FILETIME()
        ok = _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime if ok else None
        alive = _kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        return alive, ticks
    finally:
        _kernel32.CloseHandle(handle)


class SingleInstanceGuard:
    def __init__(self, lock_path: Path | str):
        self.lock_path = Path(lock_path)

    def acquire(self) -> None:
        """确保没有其他实例在运行；否则抛 InstanceAlreadyRunning。"""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        record = self._read()
        if record:
            pid = int(record.get("pid", 0))
            stored_creation = record.get("creation")
            alive, creation = _process_identity(pid)
            if alive and (stored_creation is None or creation == stored_creation or creation is None):
                # 存活且创建时间一致 → 真的在跑；无法核验创建时间 → 保守拒绝
                raise InstanceAlreadyRunning(pid)
            logger.info("发现过期锁（PID %s，进程已退出或 PID 被复用），覆盖", pid)
        _, my_creation = _process_identity(os.getpid())
        self.lock_path.write_text(
            json.dumps(
                {"pid": os.getpid(), "creation": my_creation, "acquired_ts": time.time()}
            ),
            encoding="utf-8",
        )
        logger.info("单实例锁已获取: %s (pid=%s)", self.lock_path, os.getpid())

    def release(self) -> None:
        """释放锁；只删除仍属于本进程的锁，避免误删新实例的锁。"""
        record = self._read()
        if record and int(record.get("pid", 0)) == os.getpid():
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
            logger.info("单实例锁已释放: %s", self.lock_path)

    def _read(self) -> dict | None:
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

"""单实例保护：互斥、释放、过期锁与 PID 复用。"""

import json
import os
import subprocess
import sys

import pytest

from companion.core.lifecycle import InstanceAlreadyRunning, SingleInstanceGuard


def test_acquire_and_release(tmp_path):
    guard = SingleInstanceGuard(tmp_path / "companion.lock")
    guard.acquire()
    assert (tmp_path / "companion.lock").exists()
    guard.release()
    assert not (tmp_path / "companion.lock").exists()


def test_second_instance_refused(tmp_path):
    first = SingleInstanceGuard(tmp_path / "companion.lock")
    first.acquire()
    second = SingleInstanceGuard(tmp_path / "companion.lock")
    with pytest.raises(InstanceAlreadyRunning) as excinfo:
        second.acquire()
    assert excinfo.value.pid == os.getpid()
    first.release()


def test_release_only_own_lock(tmp_path):
    lock = tmp_path / "companion.lock"
    # 锁属于别的 PID（如已死的旧实例）→ 本实例不得删除
    lock.write_text(json.dumps({"pid": 4, "creation": 12345, "acquired_ts": 0}), encoding="utf-8")
    guard = SingleInstanceGuard(lock)
    guard.release()
    assert lock.exists()


def test_stale_lock_of_dead_pid_overwritten(tmp_path):
    # 用一个已退出的子进程 PID 写入过期锁
    probe = subprocess.Popen(
        [sys.executable, "-c", "print(1)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    dead_pid = probe.pid
    probe.wait()
    lock = tmp_path / "companion.lock"
    lock.write_text(json.dumps({"pid": dead_pid, "creation": 12345, "acquired_ts": 0}), encoding="utf-8")
    guard = SingleInstanceGuard(lock)
    guard.acquire()  # 进程已死 → 覆盖成功
    guard.release()


def test_pid_reuse_mismatch_overwritten(tmp_path):
    # 存活进程但创建时间不符（PID 被复用）→ 覆盖
    lock = tmp_path / "companion.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "creation": 12345, "acquired_ts": 0}), encoding="utf-8")
    guard = SingleInstanceGuard(lock)
    guard.acquire()
    guard.release()


def test_lock_without_creation_refused_conservatively(tmp_path):
    # 旧格式锁（无创建时间）：存活即拒绝，保守处理
    lock = tmp_path / "companion.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "acquired_ts": 0}), encoding="utf-8")
    guard = SingleInstanceGuard(lock)
    with pytest.raises(InstanceAlreadyRunning):
        guard.acquire()

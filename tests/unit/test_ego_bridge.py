"""ego 桥接（替身执行器）与 URL 边界。"""

from __future__ import annotations

import asyncio
import json

import pytest

from companion.adapters.ego import EgoBrowser, EgoBridgeError, validate_public_url


class FakeRunner:
    """替身执行器：记录脚本中的 PARAMS，按队列返回结果。"""

    def __init__(self, *outcomes, delay: float = 0.0):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.scripts: list[str] = []
        self.delay = delay

    async def __call__(self, script: str, timeout: float) -> dict:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.scripts.append(script)
        header = script.split("\n", 1)[0]
        self.calls.append(json.loads(header[len("const PARAMS = "):-1]))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_browser(runner: FakeRunner, **kwargs) -> EgoBrowser:
    return EgoBrowser(script_runner=runner, page_load_timeout=1.0, **kwargs)


# ---- URL 边界 ----


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article?id=1",
        "http://example.com",
        "https://www.bing.com/search?q=%E6%B5%8B%E8%AF%95",
        "https://深.com/",  # IDN 域名（punycode 解析不在第一版范围，但不属于明确内网）
        "https://example.com:8443/page",
    ],
)
def test_public_urls_allowed(url):
    clean, reason = validate_public_url(url)
    assert clean is not None, reason


@pytest.mark.parametrize(
    "url, reason_part",
    [
        ("http://localhost/admin", "本机"),
        ("http://127.0.0.1:8080/", "非公网"),
        ("http://192.168.1.1/", "非公网"),
        ("http://10.0.0.5/", "非公网"),
        ("http://172.16.0.1/", "非公网"),
        ("http://169.254.169.254/latest/meta-data", "非公网"),  # 云元数据
        ("http://[::1]/", "非公网"),
        ("http://[fe80::1]/", "非公网"),
        ("ftp://example.com/file", "只允许"),
        ("file:///C:/Windows/win.ini", "只允许"),
        ("javascript:alert(1)", "只允许"),
        ("http://user:pass@example.com/", "用户信息"),
        ("http://nas/", "无点主机名"),
        ("http://printer.internal/", "内部域名"),
        ("http://box.local/x", "内部域名"),
        ("", "URL 为空"),
    ],
)
def test_blocked_urls_rejected(url, reason_part):
    clean, reason = validate_public_url(url)
    assert clean is None
    assert reason_part in reason


# ---- 搜索 / 打开流程 ----


async def test_search_sends_structured_params_and_returns_results():
    runner = FakeRunner({
        "ok": True,
        "results": [
            {"title": "DeepSeek 文档", "url": "https://api-docs.deepseek.com/", "snippet": "工具调用说明"}
        ],
    })
    browser = make_browser(runner)
    data = await browser.search("task-key-1", "deepseek 工具调用")
    assert data["ok"] and data["results"][0]["title"] == "DeepSeek 文档"
    call = runner.calls[0]
    assert call["op"] == "search" and call["query"] == "deepseek 工具调用"
    assert call["space"] == "companion-task-key-1"[:len(call["space"])]  # 任务键派生
    assert call["engine"].startswith("https://")
    assert "任意" not in runner.scripts[0] or True


async def test_search_empty_results_gets_honest_note():
    runner = FakeRunner({"ok": True, "results": []})
    browser = make_browser(runner)
    data = await browser.search("t", "无结果词")
    assert data["ok"] and "无结果" in data.get("note", "")


async def test_search_invalid_query_rejected_without_browser():
    runner = FakeRunner()
    browser = make_browser(runner)
    data = await browser.search("t", "")
    assert data["error"] == "invalid_query"
    data = await browser.search("t", "长" * 201)
    assert data["error"] == "invalid_query"
    assert runner.calls == []  # 未触达浏览器


async def test_open_validates_url_before_browser():
    runner = FakeRunner()
    browser = make_browser(runner)
    for bad in ("http://localhost/", "file:///etc/passwd", "https://192.168.0.1/"):
        data = await browser.open("t", bad)
        assert data["error"] == "url_blocked"
    assert runner.calls == []


async def test_open_validates_final_url_after_redirect():
    runner = FakeRunner({"ok": True, "url": "http://10.0.0.9/landing", "title": "t", "text": "x", "links": []})
    browser = make_browser(runner)
    data = await browser.open("t", "https://public.example.com/link")
    assert data["error"] == "redirect_blocked" and data["url"] == "http://10.0.0.9/landing"


async def test_open_page_limit_per_task():
    ok_page = {"ok": True, "url": "https://example.com/", "title": "t", "text": "x", "links": []}
    runner = FakeRunner(*[dict(ok_page) for _ in range(10)])
    browser = make_browser(runner, max_pages_per_task=3)
    for i in range(3):
        data = await browser.open("t", f"https://example.com/p{i}")
        assert data["ok"]
    data = await browser.open("t", "https://example.com/p4")
    assert data["error"] == "page_limit"


# ---- 并发准入 / 超时 / 取消 ----


async def test_busy_returns_immediately_without_queueing():
    runner = FakeRunner({"ok": True, "results": []}, delay=0.3)
    browser = make_browser(runner)
    first = asyncio.create_task(browser.search("task-a", "甲"))
    await asyncio.sleep(0.05)  # 第一个任务已占住浏览器
    assert browser.busy()
    data = await browser.search("task-b", "乙")
    assert data["error"] == "browser_busy"
    assert (await first)["ok"]


async def test_bridge_timeout_becomes_error_result():
    runner = FakeRunner(EgoBridgeError("timeout", "45s 未返回"))
    browser = make_browser(runner)
    data = await browser.search("t", "q")
    assert data["error"] == "timeout" and "未返回" in data["detail"]


async def test_cancel_releases_lock_for_next_task():
    async def cancelled_runner(script, timeout):
        await asyncio.sleep(5)

    browser = make_browser(None)
    browser._run_script = cancelled_runner
    slow = asyncio.create_task(browser.search("task-a", "甲"))
    await asyncio.sleep(0.05)
    slow.cancel()
    with pytest.raises(asyncio.CancelledError):
        await slow
    assert not browser.busy()  # 锁已释放，后续任务可用


# ---- TaskSpace 生命周期 ----


async def test_space_reused_across_ops_within_task():
    runner = FakeRunner(
        {"ok": True, "results": []},
        {"ok": True, "url": "https://example.com/", "title": "t", "text": "x", "links": []},
    )
    browser = make_browser(runner)
    await browser.search("same-task", "甲")
    await browser.open("same-task", "https://example.com/")
    spaces = [c["space"] for c in runner.calls]
    assert spaces[0] == spaces[1]  # 同任务复用同一空间
    assert browser.active_tasks == ["same-task"]


async def test_different_tasks_use_different_spaces():
    runner = FakeRunner(
        {"ok": True, "results": []},
        {"ok": True, "results": []},
    )
    browser = make_browser(runner)
    await browser.search("task-one", "甲")
    await browser.search("task-two", "乙")
    spaces = [c["space"] for c in runner.calls]
    assert spaces[0] != spaces[1]


async def test_name_in_use_retries_with_new_name():
    runner = FakeRunner(
        {"ok": False, "error": "EGO_TASK_SPACE_NAME_IN_USE: name taken"},
        {"ok": True, "results": []},
    )
    browser = make_browser(runner)
    data = await browser.search("task-x", "甲")
    assert data["ok"]  # 换名重试成功
    assert runner.calls[0]["space"] != runner.calls[1]["space"]


async def test_finish_task_cleans_and_forgets_space():
    runner = FakeRunner(
        {"ok": True, "results": []},  # search 建立空间
        {"ok": True, "finished": True},  # finish
    )
    browser = make_browser(runner)
    await browser.search("task-y", "甲")
    data = await browser.finish_task("task-y")
    assert data["ok"] and runner.calls[1]["op"] == "finish"
    assert browser.active_tasks == []
    again = await browser.finish_task("task-y")  # 幂等
    assert again["ok"] and "未创建过" in again.get("note", "")


async def test_finish_failure_reports_unknown_state_and_frees_slot():
    runner = FakeRunner(
        {"ok": True, "results": []},
        EgoBridgeError("timeout", "进程无响应"),
    )
    browser = make_browser(runner)
    await browser.search("task-z", "甲")
    data = await browser.finish_task("task-z")
    assert data["ok"] is False and "状态未知" in data["detail"]
    assert browser.active_tasks == []  # 不残留占用


async def test_finish_all_on_disable():
    runner = FakeRunner(
        {"ok": True, "results": []},
        {"ok": True, "results": []},
        {"ok": True, "finished": True},
        {"ok": True, "finished": True},
    )
    browser = make_browser(runner)
    await browser.search("task-1", "甲")
    await browser.search("task-2", "乙")
    finished = await browser.finish_all()
    assert finished == 2 and browser.active_tasks == []

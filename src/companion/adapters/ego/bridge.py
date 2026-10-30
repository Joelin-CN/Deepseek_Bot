"""ego-lite 浏览器桥接：固定脚本模板 + 结构化参数的子进程调用。

机制（对照 ego-browser v2.1.0 实测）：``ego-browser nodejs`` 从 stdin 读
入一段 Node ESM 脚本执行，每次调用都是一个新进程；TaskSpace 与页面在
守护侧跨进程持久，按名恢复；``console.log`` 输出经 **stderr** 返回；
脚本不继承 shell 工作目录与环境变量。

安全与边界：

- 模型输入只作为 **JSON 数据**（``PARAMS``）注入固定脚本模板，模型与
  插件都不能提交任意 JavaScript/shell/文件读取代码；
- 浏览器动作（导航、搜索、提取、结束）全部是适配器维护的固定流程；
- URL 先经 ``urlguard`` 校验，导航后的实际地址再校验一次；
- 并发准入：同一时刻只允许一个浏览器任务（``browser_busy`` 立即失败，
  不排队占用 worker）；
- 取消/超时会终止子进程；已发出的浏览器动作可能仍在执行——清理时用
  独立进程执行 ``finish`` 关闭任务空间，失败则如实报告"状态未知"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from companion.adapters.ego.urlguard import validate_public_url

logger = logging.getLogger(__name__)

_RESULT_MARK = "__EGO_RESULT__"

#: 固定桥接脚本：参数是数据不是代码；evaluate 回调不引用外部变量
_BRIDGE_SCRIPT = """\
const P = PARAMS;
const MARK = "__EGO_RESULT__";
function out(obj) { console.log(MARK + JSON.stringify(obj)); }

function decodeBing(href) {
  try {
    const u = new URL(href);
    if (u.hostname.endsWith("bing.com") && u.pathname === "/ck/a") {
      const enc = u.searchParams.get("u") || "";
      if (enc.startsWith("a1")) {
        const b64 = enc.slice(2).replace(/-/g, "+").replace(/_/g, "/");
        return decodeURIComponent(escape(atob(b64)));
      }
    }
  } catch (e) {}
  return href;
}

try {
  const task = await taskSpace(P.space);
  const page = task.page("p1");

  if (P.op === "search") {
    await page.goto(P.engine + encodeURIComponent(P.query));
    await page.waitForLoadState("load");
    const items = await page.evaluate(
      ({ limit }) => {
        const decode = (href) => {
          try {
            const u = new URL(href);
            if (u.hostname.endsWith("bing.com") && u.pathname === "/ck/a") {
              const enc = u.searchParams.get("u") || "";
              if (enc.startsWith("a1")) {
                const b64 = enc.slice(2).replace(/-/g, "+").replace(/_/g, "/");
                return decodeURIComponent(escape(atob(b64)));
              }
            }
          } catch (e) {}
          return href;
        };
        const nodes = [...document.querySelectorAll("#b_results > li.b_algo")].slice(0, limit);
        return nodes
          .map((el) => ({
            title: (el.querySelector("h2")?.innerText || "").trim().slice(0, 120),
            url: decode(el.querySelector("h2 a")?.href || ""),
            snippet: (el.querySelector(".b_caption p, p")?.innerText || "").trim().slice(0, 200),
          }))
          .filter((it) => it.title && it.url && it.url.startsWith("http"));
      },
      { limit: P.limit }
    );
    out({ ok: true, query: P.query, results: items });
  } else if (P.op === "open") {
    await page.goto(P.url);
    await page.waitForLoadState("load");
    const data = await page.evaluate(
      ({ textLimit, linkLimit }) => {
        const links = [...document.querySelectorAll("a[href]")]
          .slice(0, linkLimit * 3)
          .map((a) => ({ text: (a.innerText || "").trim().slice(0, 80), href: a.href }))
          .filter((l) => l.text && /^https?:/.test(l.href))
          .slice(0, linkLimit);
        return {
          title: (document.title || "").slice(0, 200),
          text: document.body ? document.body.innerText || "" : "",
          links,
        };
      },
      { textLimit: P.textLimit, linkLimit: P.linkLimit }
    );
    out({
      ok: true,
      url: await page.url(),
      title: data.title,
      text: data.text.slice(0, P.textLimit),
      links: data.links,
    });
  } else if (P.op === "finish") {
    await task.finish({ keep: [] });
    out({ ok: true, finished: true });
  } else {
    out({ ok: false, error: "unknown_op", detail: String(P.op) });
  }
} catch (err) {
  out({ ok: false, error: String((err && err.message) || err) });
}
"""


class EgoBridgeError(Exception):
    """桥接失败：子进程错误、超时或无结构化输出。"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"ego bridge {kind}: {detail}")
        self.kind = kind
        self.detail = detail


ScriptRunner = Callable[[str, float], Awaitable[dict[str, Any]]]


class EgoBrowser:
    """一个任务一个 TaskSpace；跨轮按名复用；浏览器全局串行（并发准入 1）。"""

    def __init__(
        self,
        *,
        command: str = "ego-browser",
        search_url: str = "https://www.bing.com/search?q=",
        page_load_timeout: float = 45.0,
        max_pages_per_task: int = 6,
        script_runner: ScriptRunner | None = None,
    ):
        self._command = command
        self._search_url = search_url
        self._page_load_timeout = page_load_timeout
        self._max_pages = max_pages_per_task
        self._run_script = script_runner or self._run_script_via_subprocess
        self._lock = asyncio.Lock()  # 浏览器串行；locked() 即忙（不排队占 worker）
        self._spaces: dict[str, str] = {}  # task_key -> space 名
        self._pages_opened: dict[str, int] = {}

    async def _run_script_via_subprocess(self, script: str, timeout: float) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            self._command, "nodejs",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(script.encode("utf-8")), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise EgoBridgeError("timeout", f"桥接进程 {timeout:.0f}s 未返回（浏览器动作可能仍在执行）")
        except asyncio.CancelledError:
            proc.kill()  # 取消时终止子进程；已发出的浏览器动作状态未知
            await proc.wait()
            raise
        text = (out + err).decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            if line.startswith(_RESULT_MARK):
                try:
                    return json.loads(line[len(_RESULT_MARK):])
                except json.JSONDecodeError as exc:
                    raise EgoBridgeError("bad_result", f"结果行不是合法 JSON：{exc}")
        tail = text.strip().splitlines()[-1][:200] if text.strip() else "(无输出)"
        raise EgoBridgeError("no_result", f"桥接进程未产出结果行；末行：{tail}")

    def busy(self) -> bool:
        return self._lock.locked()

    def _space_for(self, task_key: str) -> str:
        if task_key not in self._spaces:
            self._spaces[task_key] = f"companion-{task_key[:12]}"
        return self._spaces[task_key]

    def _script(self, params: dict[str, Any]) -> str:
        # 参数以 JSON 数据嵌入固定模板；json.dumps 输出是合法 JS 表达式，模型无法注入代码
        return "const PARAMS = " + json.dumps(params, ensure_ascii=True) + ";\n" + _BRIDGE_SCRIPT

    async def _op(self, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        data = await self._run_script(self._script(params), timeout)
        if not isinstance(data, dict) or "ok" not in data:
            raise EgoBridgeError("bad_result", f"结果结构异常：{str(data)[:200]}")
        return data

    async def run(self, op: str, task_key: str, *, timeout: float | None = None, **params: Any) -> dict[str, Any]:
        """执行一个浏览器操作；忙时立即返回 browser_busy（不等待）。"""
        if self._lock.locked():
            return {"ok": False, "error": "browser_busy", "detail": "已有浏览器任务在执行，请稍后重试"}
        async with self._lock:
            return await self._run_locked(op, task_key, timeout=timeout, **params)

    async def _run_locked(
        self, op: str, task_key: str, *, timeout: float | None = None, **params: Any
    ) -> dict[str, Any]:
        timeout = timeout or (self._page_load_timeout + 15.0)
        params = {"op": op, "space": self._space_for(task_key), **params}
        try:
            data = await self._op(params, timeout=timeout)
        except EgoBridgeError as exc:
            return {"ok": False, "error": exc.kind, "detail": exc.detail}
        if not data.get("ok") and "EGO_TASK_SPACE_NAME_IN_USE" in str(data.get("error", "")):
            # 空间名被用户占用：换名重试一次（此时任务尚未建立，不产生半成品资源）
            self._spaces[task_key] = f"companion-{task_key[:12]}-{len(self._spaces) + 1}"
            params["space"] = self._spaces[task_key]
            logger.info("TaskSpace 名称被占用，换名重试: task=%s space=%s", task_key[:8], self._spaces[task_key])
            try:
                data = await self._op(params, timeout=timeout)
            except EgoBridgeError as exc:
                return {"ok": False, "error": exc.kind, "detail": exc.detail}
        if op in {"search", "open"} and data.get("ok"):
            self._pages_opened[task_key] = self._pages_opened.get(task_key, 0) + 1
        return data

    # ---- 面向插件的操作 ----

    async def search(self, task_key: str, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 200:
            return {"ok": False, "error": "invalid_query", "detail": "查询词为空或超过 200 字符"}
        data = await self.run(
            "search", task_key, query=query, engine=self._search_url, limit=6,
        )
        if data.get("ok") and not data.get("results"):
            data["note"] = "搜索无结果：可能遇到验证码、站点阻挡或查询词无匹配"
        return data

    async def open(self, task_key: str, url: str) -> dict[str, Any]:
        clean, reason = validate_public_url(url)
        if clean is None:
            return {"ok": False, "error": "url_blocked", "detail": reason}
        if self._pages_opened.get(task_key, 0) >= self._max_pages:
            return {
                "ok": False, "error": "page_limit",
                "detail": f"本任务打开页数已达上限 {self._max_pages}，请归纳现有内容",
            }
        data = await self.run("open", task_key, url=clean, textLimit=12000, linkLimit=15)
        if data.get("ok"):
            final_url = data.get("url", "")
            final_clean, final_reason = validate_public_url(final_url)
            if final_clean is None:  # 跳转目标越界：如实报告，不伪造结果
                return {
                    "ok": False, "error": "redirect_blocked",
                    "detail": f"页面跳转到了被拒绝的地址：{final_reason}",
                    "url": final_url,
                }
        return data

    async def finish_task(self, task_key: str) -> dict[str, Any]:
        """结束并清理该任务的 TaskSpace（幂等；不关闭用户自己的页面）。"""
        if task_key not in self._spaces:
            return {"ok": True, "finished": True, "note": "该任务未创建过空间"}
        try:
            data = await self.run("finish", task_key, timeout=20.0)
        except EgoBridgeError as exc:
            data = {"ok": False, "error": exc.kind, "detail": exc.detail}
        self._spaces.pop(task_key, None)
        self._pages_opened.pop(task_key, None)
        if not data.get("ok"):
            logger.warning(
                "任务空间清理失败（状态未知）: task=%s error=%s", task_key[:8], data.get("error")
            )
            return {
                "ok": False, "error": data.get("error"),
                "detail": f"清理失败，任务空间状态未知：{data.get('detail', '')}",
            }
        return data

    async def finish_all(self) -> int:
        """停用/退出时清理全部空间；返回成功数（失败记日志，状态未知）。"""
        finished = 0
        for task_key in list(self._spaces):
            result = await self.finish_task(task_key)
            if result.get("ok"):
                finished += 1
        return finished

    @property
    def active_tasks(self) -> list[str]:
        return list(self._spaces)

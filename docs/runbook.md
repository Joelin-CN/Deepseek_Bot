# 运行手册

## 当前状态（2026-10-29 M5 更新）

M1（QQ 私聊固定应答）、M2（DeepSeek 实际应答）、M3/M3.1（统一会话内核 + 基础长期记忆及修正轮）、M4（原生插件系统与公共 API）与 M5（统一工具执行循环 + ego-lite 网页任务）均已实现并通过验收。当前运行于 deepseek 模式。

M5 变化：DeepSeek tool_calls 多轮闭环（模型请求工具 → 框架校验权限/参数 → 执行 → 按调用 ID 回传 → 模型继续，直到最终答复或达到限制）；任务控制命令 `/task`（锁外处理，长任务期间可取消）；网页任务插件 `builtin.ego_browser`（`web_search`/`web_open`/`web_finish`，仅主人、仅公开网页，经本机 ego-lite 执行）。

M4 变化：原生插件系统（`companion.api` 公共接口 + 插件宿主），内置 `/hello`、`/counter` 插件与主人管理命令 `/plugin`；插件 KV 与启停状态持久化在 `data/bot.db`；未被插件消费的消息继续走记忆命令与模型应答（原链路不变）。

M3 内核变化：每次重连自动重新验证机器人身份；动作结果三分（成功/明确失败/结果未知不重发）；事件队列与模型并发有上限；消息状态与模型用量落库（`data/bot.db`）。

## 已验证的组件与版本组合

| 组件 | 版本 / 位置 |
| --- | --- |
| Windows | 11 专业版 10.0.26100 x64 |
| QQ NT | `C:\Program Files\Tencent\QQNT`，基础 9.9.29-47354 + 热更新 9.9.36-53644（SnowLuma WebUI 显示 `available`，实测注入正常） |
| SnowLuma | v2.2.1 Windows 完整版（自带 Node 运行时），安装于 `E:\Softwares\SnowLuma` |
| OneBot 接口 | 正向 WS `ws://127.0.0.1:3001/`，role=Universal，messageFormat=array，Bearer Token 鉴权；同机 HTTP 3000 已禁用，WebUI 5099 仅回环 |
| Python | Conda `Deepseek_bot`，3.12.16（`E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe`） |
| 应用依赖 | websockets / httpx / pydantic / pydantic-settings（经 `pip install -e ".[dev]"` 安装，见 [环境约定](development-environment.md)） |
| DeepSeek | 官方 API `https://api.deepseek.com`，模型 `deepseek-flash`，思考模式默认关闭 |

## 配置文件

| 文件 | 作用 | Git |
| --- | --- | --- |
| `config/local.json` | OneBot 地址/Token、机器人 QQ 号、授权发送者列表、`reply_mode`（fixed/deepseek） | 忽略 |
| `.env` | `DEEPSEEK_API_KEY` | 忽略 |
| `config/bot.example.json` / `.env.example` | 模板 | 跟踪 |

配置错误会在启动时明确报告并退出（exit code 2），提示缺少哪个文件或哪个字段不合法。

## 数据目录与备份（M3 起）

- 数据库：`data/bot.db`（SQLite 3.54.1，WAL 模式；同目录有 `-wal`/`-shm` 文件）。位置由配置解析：默认仓库 `data/`，`COMPANION_DATA_DIR` 环境变量或 `bot.data_dir` 可覆盖；不依赖启动目录。
- 迁移：启动时自动按版本应用（当前 v1 messages/usage、v2 memories、v3 messages 全文列、v4 plugin_kv/plugin_runtime_state），重复启动不重复应用。
- 表：`messages`（消息状态链：received→model_generated→sent_ok/send_failed/send_unknown/consumed，consumed 为被插件消费且无需回复）、`usage`（逐次模型 token）、`memories`（长期记忆）、`plugin_kv`（插件键值存储）、`plugin_runtime_state`（插件运行时启停状态）。
- 插件数据目录：`data/plugins/data/<插件ID>/`（各插件独立，含其 KV 之外的任意文件）。
- 备份：停止机器人后复制整个 `data/` 目录即可；恢复时原样放回（含迁移记录，可直接启动）。`send_unknown` 表示消息可能已送达但回执未知——按“可能已发送”处理，不要据此重发。
- `data/` 已被 Git 忽略，不入库。

## 对话上下文与记忆行为（M3.1 起）

- **内存中的短期历史有界**（按消息条数，当前为 2×max_history_messages），不只裁剪请求副本。
- **重启恢复**：messages 表保存入站与已送达回复的全文；新进程首次访问某会话时，从库中恢复最近若干轮**已完整送达**的对话（发送失败、回执未知、命令回执不构成对话历史）。
- **送达才入史**：只有 `sent_ok` 的回复进入后续对话；回执未知/发送失败/发送前截断的内容不会以“完整已送达回复”出现（历史记录实际送达文本）。
- **模型输入总预算**（`max_input_tokens`，默认 8000）覆盖系统提示+长期记忆+历史+当前消息；超限时先丢最旧记忆、再裁历史，当前消息独占超限则截断并标注。
- **忘记/修改记忆**后：该记忆立即从检索与上下文注入消失，同时**清空本会话短期对话上下文**（避免旧问答继续引用已删记忆）；命令回复会说明这一点。
- 记忆命令、数据目录与备份见下文对应章节。

## 长期记忆命令（私聊内直接发送）

| 命令 | 作用 |
| --- | --- |
| `记住：内容` | 新增一条本会话记忆（重启后仍有效） |
| `查看记忆 [页码]` | 分页列出本会话记忆 |
| `搜索记忆 关键词` | 中文子串检索 |
| `修改记忆 #编号：新内容` | 修订（产生新编号，旧编号失效） |
| `忘记 #编号` | 删除该条记忆（立即从上下文消失） |

记忆只作用于当前私聊会话；不同私聊/群之间互不可见。写操作需要 MEMORY_MANAGE 能力（主人/授权成员），身份来自 QQ 号与本地配置，不接受聊天文字声明。

## 插件系统（M4 起）

内置插件命令（需在配置中启用或经管理命令启用）：

| 命令 | 来源 | 作用 |
| --- | --- | --- |
| `/hello` | builtin.hello | 打招呼并展示当前会话与身份信息（来自运行上下文） |
| `/counter` | builtin.counter | 会话计数（KV 持久化，跨重启保留）；`/counter reset` 重置本会话计数 |

插件管理命令（**仅主人**，私聊发送；非主人收到拒绝回复）：

| 命令 | 作用 |
| --- | --- |
| `/plugin list` | 列出发现的插件与状态（ENABLED/DISABLED/FAILED 及错误原因） |
| `/plugin status <插件ID>` | 查看单个插件的注册情况（命令/工具/任务数）与错误 |
| `/plugin enable <插件ID>` | 启用（必要时先重新加载）并持久化 |
| `/plugin disable <插件ID>` | 停用（撤销命令/处理器/工具、取消后台任务）并持久化 |

启用状态规则：**数据库运行状态行优先；无行则取 `config/local.json` 的 `plugins.instances.<ID>.enabled`（缺省停用）**。配置文件决定首次启动，之后管理命令的显式操作一直有效；进程正常退出不改写状态。

配置示例（`config/local.json`）：

```json
"plugins": {
  "extra_roots": [],
  "instances": {
    "builtin.hello": {"enabled": true, "config": {}},
    "builtin.counter": {"enabled": true, "config": {"heartbeat_interval": 60}}
  }
}
```

counter 的 `heartbeat_interval` 只控制写本地日志的心跳（默认 60 秒，0 关闭），**不会**定时向 QQ 发送消息。

故障排查：插件加载/启用失败不影响的其余插件与正常聊天；`/plugin list` 与 `logs/bot.log`（`companion.plugin.<ID>`）给出具体原因（重复 ID、入口错误、API 版本不兼容、依赖缺失、配置非法等）。修复文件后重启或再次 `/plugin enable` 即重新加载。

## 工具循环与网页任务（M5 起）

### 任务控制命令（仅主人，私聊；在会话锁之外处理，长任务期间可用）

| 命令 | 作用 |
| --- | --- |
| `/task list` | 列出任务（状态 RUNNING/COMPLETED/FAILED/CANCELLED/TIMEOUT、耗时、轮次、工具次数、tokens） |
| `/task status <ID>` | 查看单个任务（支持 ID 前缀） |
| `/task cancel <ID>` | 取消自己发起的运行中任务（传导到模型循环、工具执行与浏览器清理） |

进入工具循环时会先发一条受理提示（含任务短 ID 与取消命令示例）。取消后不再发起下一轮模型/工具请求，不会发送迟到的成功答复。

### 工具循环配置（`config/local.json` 的 `tools` 段）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enable` | true | 工具循环总开关（false 时纯聊天直答） |
| `max_model_rounds` | 5 | 单任务模型请求轮数上限 |
| `max_tool_calls` | 8 | 单任务工具执行总次数上限 |
| `tool_timeout` | 60 | 单次工具执行超时（秒） |
| `task_timeout` | 300 | 单任务总时限（秒） |
| `max_tool_result_chars` | 6000 | 单个工具结果序列化字符上限（结构保持式裁剪） |
| `accept_notice` | true | 进入工具循环时发送一次受理提示 |

每轮模型请求都计入 `max_input_tokens` 总预算并逐轮记录用量（usage 表 `source='chat_tools'`）。达到限制时明确告知"任务未完成"，不冒充成功。普通聊天无工具时直答，行为与之前一致。

### 网页任务（builtin.ego_browser 插件，仅主人）

- 能力：`web_search`（Bing 固定搜索流程）→ `web_open`（打开公网页面读正文/链接）→ `web_finish`（结束清理）；模型按需组合。
- 用法示例：私聊发"搜一下 DeepSeek 最新模型并总结要点（附来源链接）"。
- 前提：本机 ego-lite 可用（`ego-browser --version` 正常返回；浏览器由 ego-lite 管理）。插件配置（`plugins.instances.builtin.ego_browser.config`）：`search_url`（默认 Bing）、`page_load_timeout`（默认 45s）、`max_pages_per_task`（默认 6）。
- 并发：网页工具全局同时只执行 1 个；忙时工具立即返回 `browser_busy`，模型会转告用户，不会排队占满处理 worker。
- 范围与边界：仅公网 HTTP/HTTPS；本机/私网/内部域名/非 HTTP 协议/URL 用户信息一律拒绝；跳转后的实际地址同样过检。DNS 重绑定不在校验范围；TaskSpace 不构成 Profile/登录态/网络隔离；不读取 Cookie、密钥、环境变量；不提供任意 JS/命令/文件工具；页面内容只作为工具数据返回。取消/超时会终止桥接子进程并用独立进程清理任务空间；清理失败时日志如实记录"空间状态未知"。
- 故障：ego-lite 不可用或页面失败时，工具返回明确错误，模型据此告知用户；普通聊天与记忆命令不受影响。网页内容不自动写入长期记忆。
- DNS 排查经验（2026-10-30 实例）：网页工具报 `net::ERR_NAME_NOT_RESOLVED` 时先分三层定位——① 代理是否真的在跑（本机 sing-box TUN 网卡 `utun12` 存在但核心进程退出=残留黑洞；`netstat` 查代理端口是否监听）；② 系统 DNS 是否选择性丢包（教育网 DNS 对 bing/google 域名超时、baidu 正常）；③ ego 浏览器链路（用 `adapters/ego` 打开百度验证浏览器健康）。修复代理后 ego 浏览器自动跟随系统代理/TUN，bot 无需改动。

## 启动前检查

0. 单实例保护（M3.1 起自动）：启动时获取 `data/companion.lock`（记录 PID 与进程创建时间，防 PID 复用误判）；已有实例运行时新实例拒绝启动（exit code 3 并提示 PID）。异常退出后锁残留属过期锁，下次启动自动覆盖；确有需要也可手动删除该文件。`stop_bot.ps1` 会核验进程身份（Deepseek_bot 环境的 python 运行 companion）后才停止。

1. SnowLuma 运行中：`E:\Softwares\SnowLuma` 下 `node.exe index.mjs`（用自带 node.exe；`launcher.bat` 依赖 PATH 里的 node，本机 24.15.0 低于完整版 engines 要求 24.22.1+）。WebUI：`http://127.0.0.1:5099`。
2. 机器人 QQ 实例已登录（第二个 QQ 窗口，账号 3666167977），且 SnowLuma 已对其注入（WebUI「进程」页该 PID 状态 online；`hookAutoLoad` 默认 false，QQ 重启后需在 WebUI 手动 Load）。
3. 端口监听：`netstat -ano | findstr :3001` 应有 127.0.0.1:3001 LISTENING。
4. 环境一致性：`conda run -n Deepseek_bot python -m pip check`。

## 启动与停止

```powershell
Set-Location 'E:\Deepseek Bot'

# 0) SnowLuma（电脑重启后需要；PID 记录在 runtime\snowluma.pid）
#    WebUI 预设密码从 .env 的 SNOWLUMA_WEBUI_PASSWORD 读取，经
#    SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD 播种——只在 webui.json 不存在时生效，
#    播种后跨重启稳定。
powershell -ExecutionPolicy Bypass -File scripts\windows\start_snowluma.ps1
#    QQ 重启后仍需登录 WebUI（http://127.0.0.1:5099，密码=.env 的
#    SNOWLUMA_WEBUI_PASSWORD），在「进程注入」页对机器人 QQ 实例
#    （3666167977）点 Load；个人 QQ（844281600）不要注入。

# 1) 机器人（前台运行推荐日常使用；Ctrl+C 优雅退出并记录清理日志）
powershell -ExecutionPolicy Bypass -File scripts\windows\start_bot.ps1 -Foreground

# 2) 后台运行（PID 记录在 runtime\bot.pid）
powershell -ExecutionPolicy Bypass -File scripts\windows\start_bot.ps1

# 停止后台实例（只结束 PID 文件记录的进程）
powershell -ExecutionPolicy Bypass -File scripts\windows\stop_bot.ps1
```

启动成功的标志（`logs/bot.log`）：`已连接 OneBot 服务` → `机器人身份确认: uin=3666167977` → `等待私聊消息…`。

## 日志与用量

- 日志：`logs/bot.log`（UTF-8）与控制台。INFO 级不记录消息正文，DEBUG 才记录回复内容；密钥永不落日志。
- 每次 DeepSeek 调用记录一行用量：`prompt_tokens / completion_tokens / total_tokens` 与模型名。
- 回复成功记录入站与回执 message_id，可与 QQ 客户端互相印证。

## 常见故障与处理

| 现象 | 处理 |
| --- | --- |
| 握手被拒（HTTP 401/403）或连上即断 | `config/local.json` 的 `onebot.access_token` 与 SnowLuma `config/onebot_3666167977.json` 中 `wsServers[0].accessToken` 不一致；或 Token 末尾混入空白 |
| 身份确认失败（self_id 不一致） | 登录的 QQ 账号与配置的 `self_id` 不同——程序会在每次重连时验证并致命退出；确认 WebUI 中 online 的账号 |
| 回复发送结果未知 | 日志 `send_unknown`：消息可能已送达，程序不会自动重发；如需补发请人工确认后手动发送 |
| 反复「连接断开」 | 检查 SnowLuma 是否运行、端口是否被占用；QQ 是否掉线 |
| DeepSeek 鉴权失败（401） | `.env` 中的 `DEEPSEEK_API_KEY` 无效 |
| 余额不足（402）／限流（429）／超时 | 程序已把明确提示发给用户；余额需到 platform.deepseek.com 充值 |
| 断线 | OneBot 客户端指数退避自动重连（1s→30s）并重新验证身份；SnowLuma/QQ 恢复后无需重启机器人 |

## SnowLuma 运维要点

- 只对机器人 QQ 实例（登录 3666167977 的那个 QQ.exe 进程）注入；个人 QQ 保持未注入状态。`runtime.json` 的 `hookAutoLoad: false` 维持现状。
- 多开第二个 QQ 实例：直接再次启动 `C:\Program Files\Tencent\QQNT\QQ.exe`（本机 QQ 支持多开），新实例会记住机器人账号登录态。
- SnowLuma 配置在 `E:\Softwares\SnowLuma\config\`（含 WebUI 密码哈希与各账号 OneBot 配置）；登录态数据也在该目录。备份/迁移时整个目录一起处理，不要提交到 Git。
- WebUI 初始密码已修改；本机留存（不在仓库中）。

## 环境检查命令

```powershell
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python --version
& 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pip check
Set-Location 'E:\Deepseek Bot'; & 'E:\Softwares\Anaconda\Scripts\conda.exe' run --no-capture-output -n Deepseek_bot python -m pytest -q
```

## 第一条联调链路（已通过，留作回归参照）

连接状态（lifecycle 事件 + `get_login_info`）→ 指定私聊接收（过滤群消息/未授权/自身）→ 去重（同 message_id 只回一次）→ 模型响应（M2）→ 回传（`send_private_msg` retcode=0 且返回 message_id）。每步的验证方式：替身服务集成测试（`tests/integration/test_onebot_stub.py`）+ 真实 QQ 验收（用户确认收到）。

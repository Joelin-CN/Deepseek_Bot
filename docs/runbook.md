# 运行手册

## 当前状态（2026-10-28 M3 更新）

M1（QQ 私聊固定应答）、M2（DeepSeek 实际应答）与 M3（统一会话内核 + 基础长期记忆）均已实现并通过验收。当前运行于 deepseek 模式：授权发送者用日常 QQ 给机器人发私聊可收到模型回复；显式记忆命令（记住/查看/搜索/修改/忘记）跨重启有效。

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
- 迁移：启动时自动按版本应用（当前 v1 messages/usage、v2 memories），重复启动不重复应用。
- 表：`messages`（消息状态链：received→model_generated→sent_ok/send_failed/send_unknown）、`usage`（逐次模型 token）、`memories`（长期记忆）。
- 备份：停止机器人后复制整个 `data/` 目录即可；恢复时原样放回（含迁移记录，可直接启动）。`send_unknown` 表示消息可能已送达但回执未知——按“可能已发送”处理，不要据此重发。
- `data/` 已被 Git 忽略，不入库。

## 长期记忆命令（私聊内直接发送）

| 命令 | 作用 |
| --- | --- |
| `记住：内容` | 新增一条本会话记忆（重启后仍有效） |
| `查看记忆 [页码]` | 分页列出本会话记忆 |
| `搜索记忆 关键词` | 中文子串检索 |
| `修改记忆 #编号：新内容` | 修订（产生新编号，旧编号失效） |
| `忘记 #编号` | 删除该条记忆（立即从上下文消失） |

记忆只作用于当前私聊会话；不同私聊/群之间互不可见。写操作需要 MEMORY_MANAGE 能力（主人/授权成员），身份来自 QQ 号与本地配置，不接受聊天文字声明。

## 启动前检查

0. 确认没有残留的旧实例（`Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object CommandLine -like '*companion*'`）——两个实例会同时应答同一账号造成重复回复；正式的单实例保护在 M9 实现。

1. SnowLuma 运行中：`E:\Softwares\SnowLuma` 下 `node.exe index.mjs`（用自带 node.exe；`launcher.bat` 依赖 PATH 里的 node，本机 24.15.0 低于完整版 engines 要求 24.22.1+）。WebUI：`http://127.0.0.1:5099`。
2. 机器人 QQ 实例已登录（第二个 QQ 窗口，账号 3666167977），且 SnowLuma 已对其注入（WebUI「进程」页该 PID 状态 online；`hookAutoLoad` 默认 false，QQ 重启后需在 WebUI 手动 Load）。
3. 端口监听：`netstat -ano | findstr :3001` 应有 127.0.0.1:3001 LISTENING。
4. 环境一致性：`conda run -n Deepseek_bot python -m pip check`。

## 启动与停止

```powershell
Set-Location 'E:\Deepseek Bot'

# 前台运行（推荐日常使用；Ctrl+C 优雅退出并记录清理日志）
powershell -ExecutionPolicy Bypass -File scripts\windows\start_bot.ps1 -Foreground

# 后台运行（PID 记录在 runtime\bot.pid）
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

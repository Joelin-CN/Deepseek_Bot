# 运行手册

## 当前状态（2026-10-28 更新）

M1（QQ 私聊固定应答）与 M2（DeepSeek 实际应答）均已实现、测试并通过真实验收：授权发送者用日常 QQ（844281600「Zero」）给机器人（3666167977「Biubiu」）发私聊，能收到回复；当前 `reply_mode=deepseek`。

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

## 启动前检查

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
| 反复「连接断开」 | 检查 SnowLuma 是否运行、端口是否被占用；QQ 是否掉线 |
| 身份确认失败（self_id 不一致） | 登录的 QQ 账号与配置的 `self_id` 不同；确认 WebUI 中 online 的账号 |
| DeepSeek 鉴权失败（401） | `.env` 中的 `DEEPSEEK_API_KEY` 无效 |
| 余额不足（402）／限流（429）／超时 | 程序已把明确提示发给用户；余额需到 platform.deepseek.com 充值 |
| 断线 | OneBot 客户端指数退避自动重连（1s→30s）；SnowLuma/QQ 恢复后无需重启机器人 |

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

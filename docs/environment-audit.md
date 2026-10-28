# 环境调查与验证记录

检查日期：2026-10-28。这里记录检查当时的状态，不替代启动前再次核对。

## 强制推进顺序

1. **调研路线要求**：阅读当前官方源码、文档和发行资料，列出操作系统、Python、QQ、SnowLuma、协议、客户端依赖和外部服务要求。
2. **盘点本机现状**：检查安装位置、版本、解释器、环境、服务和端口，先复用合适的现有安装。
3. **校验兼容与缺口**：比较要求和现状，明确可复用、需配置、需安装以及尚待实际验证的项目。
4. **配置对应阶段环境**：使用固定 Conda 环境，安装或配置当前阶段确实需要的组件。
5. **执行环境验收**：以实际命令与连接结果确认，记录证据和未完成项。
6. **再开始实现和联调**：没有通过对应前置条件的步骤不得宣称完成。进入下一里程碑前再校验新增依赖和配置。

这是按里程碑执行的顺序。M1 固定回复不依赖模型密钥；M2 才增加 DeepSeek 配置和模型验证。

## 路线要求与本机现状

| 项目 | 要求或计划 | 本次检查结果 |
| --- | --- | --- |
| Windows | Windows 原生运行、64 位 | Windows 11 专业版，10.0.26100，64 位 |
| Shell | 可执行本机命令 | PowerShell 7.6.5 |
| Git | 项目版本管理 | 2.54.0.windows.1 |
| Python | 固定使用 Conda `Deepseek_bot`，Python 3.12.x | 已创建；实际 Python 3.12.16 |
| Conda | 可明确定位的本机安装 | 25.7.0，`E:\Softwares\Anaconda` |
| pip | 必须属于项目环境 | 26.2.2；路径属于 `Deepseek_bot`；`pip check` 通过 |
| Node | SnowLuma Lite 当前 README 要求 24+；完整版自带运行时 | 本机 v24.15.0；尚未安装 SnowLuma |
| QQ | 核对 SnowLuma 对当前 QQ 构建的支持 | 本机 QQ 9.9.20.37625；与目标 SnowLuma 的兼容尚未验证 |
| SnowLuma | Windows x64、独立机器人账号、OneBot 接入 | 未安装、未登录；检查时最新正式版为 v2.2.1 |
| 默认端口 | 部署前检查 3000、3001、5099；最终以配置为准 | 检查时未发现监听占用 |
| 项目密钥配置 | 本地配置，不能进入 Git | `.env` 尚不存在；当前进程未见相关业务变量；未读取或输出密钥内容 |
| DeepSeek | 官方 API、可用模型、鉴权和额度 | 尚未配置或验证，M2 前处理 |
| ego-lite | 后续浏览器里程碑验证 | 命令已存在；本次未联调，不是 M1/M2 的实现前置条件 |

## 本轮已验证的基础环境

- `conda run -n Deepseek_bot` 使用目标环境解释器。
- `sys.prefix` 指向 `E:\Softwares\Anaconda\envs\Deepseek_bot`。
- Python 版本属于 3.12 系列，SSL 和 SQLite 基础模块可导入。
- pip 来自目标环境，`pip check` 未发现依赖破损。
- 当前 PowerShell 会话激活后，环境名与解释器路径正确。
- GitHub API 可读取参考项目的源码清单和发布资料。

以上不等价于 SnowLuma、QQ 或 DeepSeek 联调通过。当前没有发送 QQ 消息，没有调用收费模型接口，没有安装应用依赖。

## 下一位 Agent 的环境前置检查

- 重新核对 SnowLuma 正式发行包与 QQ 版本支持；不能只根据项目名称或旧教程判断兼容。
- 以当前发布页为准。本次 v2.2.1 的 Windows 资产为 ZIP；不要硬套旧文档的 tar.gz 文件名。
- 先明确独立机器人账号及 QQ 实例，显式检查自动加载设置，避免接入其他账号。
- 检查当前阶段依赖的 Python 版本支持、Windows 支持、异步接口及相互兼容性，再安装进 `Deepseek_bot`。
- 检查本机监听地址、端口、Token、消息格式和 WebSocket 角色，再连接 OneBot。
- M2 前在本地配置 DeepSeek 密钥，验证实际可用模型、鉴权与错误处理；网络可达不等于鉴权成功。

## 2026-10-28 实现与联调阶段的复验结果（M1/M2）

在骨架阶段记录的基础上，本轮按同一顺序复验并完成配置与联调：

| 项目 | 要求 | 本机现状 | 检查结果 | 处理方式 |
| --- | --- | --- | --- | --- |
| SnowLuma 发行包 | Windows x64 完整版，以发布页为准 | v2.2.1 ZIP（43MB，自带 node.exe）下载自 GitHub Releases | 完整性校验通过（72 条目无损坏） | 解压至 `E:\Softwares\SnowLuma` |
| SnowLuma 对 QQ 构建 | 支持当前 QQ NT | QQ 基础 9.9.29-47354 + 热更新 9.9.36-53644（此前审计记为 9.9.20-37625，实际运行版本更高） | WebUI 进程页显示 `available`，注入后 `online` | 直接使用，无需改 QQ 版本 |
| 自动加载设置 | 只接入机器人账号 | v2.2.1 默认 `hookAutoLoad: false` | 手动注入仅第二个 QQ 实例（PID 50996），个人 QQ（844281600）保持未注入 | 维持 false |
| OneBot 服务器配置 | 本机回环 + Token + 数组消息段 | 自动生成 `onebot_3666167977.json`：WS 127.0.0.1:3001 Universal array，Token 已生成 | 符合要求 | 禁用未用的 HTTP 3000 服务端 |
| WS 探针 | get_login_info 返回机器人 UIN | 探针连接 3001 成功 | `retcode=0`，UIN=3666167977「Biubiu」；`get_version_info`：SnowLuma 2.2.1 / OneBot v11；lifecycle/心跳事件正常 | 环境验收通过 |
| Python 应用依赖 | Windows + 3.12 + 异步兼容 | websockets 17.3、httpx 0.28.1、pydantic 2.14.1、pydantic-settings 2.16.1、pytest 9.2、pytest-asyncio 1.4.1 | `pip check` 通过，全部可导入 | 经 `pyproject.toml` 安装进 `Deepseek_bot` |
| DeepSeek 模型与鉴权 | 官方 API、有效 Key | 当前官方模型为 `deepseek-flash` / `deepseek-v4-pro`（旧名 deepseek-chat/reasoner 已从文档移除，不采用） | 真实调用 HTTP 200；假 Key 得到 401 并正确映射为鉴权错误提示 | 默认 `deepseek-flash`，思考模式关闭 |
| M1 真实验收 | 指定发送者私聊收到固定应答一次 | 授权发送者 844281600 与机器人互为好友 | 用户确认收到「收到，QQ 链路已连通。」；日志含入站/回执 message_id（retcode=0） | 通过 |
| M2 真实验收 | QQ 中收到模型生成回复 | reply_mode=deepseek | 用户确认收到模型生成回复；日志记录两次调用的 usage（第二次 prompt_tokens 增加，证明有界短期上下文生效） | 通过 |

ego-lite 浏览器接入仍未开始，按计划属于后续里程碑，不影响 M1/M2 结论。

## 2026-10-28 M3（内核与基础记忆）复验结果

| 项目 | 要求 | 本机现状 | 检查结果 | 处理方式 |
| --- | --- | --- | --- | --- |
| 数据库能力 | SQLite、WAL、事务、迁移 | 标准库 sqlite3（SQLite 3.54.1，随 Python 3.12.16），FTS3/4/5 可用 | WAL 建库、并发写（50 协程）、事务回滚、重开恢复测试全部通过 | 不引入第三方数据库依赖；WAL 模式 |
| 中文检索 | 显式记忆量级的可靠匹配 | v1 采用 LIKE 子串（通配符转义），FTS5 留作升级路径 | 中文关键词/含 %\_ 的检索用例通过 | 不依赖分词，不预装向量库 |
| 应用依赖 | 不新增 | 无新增第三方包（sqlite3 为标准库） | `pip check` 通过；115 项测试（单元+替身集成）全绿 | 保持既有依赖集 |
| 环境与进程 | 复用 M1/M2 链路 | SnowLuma/QQ/Conda 环境未变 | 未重装任何组件；机器人重启后自动迁移数据库（v1+v2） | 按里程碑推进，先测试后联调 |
| 数据目录 | Git 忽略、不依赖 CWD | `data/bot.db`（路径由配置解析，`COMPANION_DATA_DIR` 可覆盖） | `data/` 在 .gitignore；配置测试覆盖三种解析路径 | 默认仓库 data/ 目录 |

M3 验收要点（全部有自动化测试）：原有 M1/M2 回归通过；私聊/群/不同用户会话不串线；每次重连重新验证身份、验证前事件不进业务队列；动作结果三分（成功/明确失败/未知不重发），服务端已执行但回执断线时不补发；超时动作不被延后误发；事件队列与模型并发有上限；历史按条数+token 裁剪、输出有 max_tokens/长度上限；迁移、重启恢复、并发访问通过；显式记忆增删改查/修订链/跨范围拒绝/上下文注入上限通过。

## 来源

- [AstrBot pyproject.toml](https://github.com/AstrBotDevs/AstrBot/blob/master/pyproject.toml)：本次读取的 Python 要求为 `>=3.12`。
- [SnowLuma README](https://github.com/SnowLuma/SnowLuma)：当前发行包和运行时要求。
- [SnowLuma v2.2.1 发布页](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.2.1)：本次读取的 Windows x64 资产。
- [Windows 部署说明](https://snowluma.github.io/zh/docs/guide/deploy/windows)
- [OneBot 配置说明](https://snowluma.github.io/zh/docs/guide/configuration)
- [DeepSeek 官方文档](https://api-docs.deepseek.com/)

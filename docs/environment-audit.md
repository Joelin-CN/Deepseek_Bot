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

## 2026-10-28 M3.1 修正轮复验结果

| 修正项 | 实现 | 检查结果 |
| --- | --- | --- |
| 内存历史真正有界 | SessionManager deque maxlen（2×max_history_messages），append_turn 强制上限 | 溢出从左侧丢弃有测试 |
| 忘记/修订后的旧上下文 | 命令成功后清空该会话短期上下文，回复中说明；失败不回调 | 回调触发/不触发路径有测试 |
| 模型输入总预算 | `max_input_tokens`（默认 8000）覆盖系统+记忆+历史+当前消息；记忆从最旧丢弃、历史按剩余预算、消息独占超限截断并标注 | 三条路径均有断言总估算不超预算 |
| 生成与送达分离 | respond 只生成不写史；sent_ok 后 commit（记录实际送达文本）；失败/未知/截断/命令回执不入史 | 单元+集成测试覆盖 |
| 单实例保护 | `data/companion.lock`（PID+创建时间核验，防 PID 复用），覆盖 python -m companion 全部入口；stop 脚本核验进程身份 | 互斥/过期锁/PID 复用/保守拒绝有测试 |
| 重启恢复有界历史 | 迁移 v3 全文列；delivered_turns 只取 sent_ok 轮次；每会话懒加载一次 | 跨 runner 重启的集成测试通过 |

138 项测试全绿；无新增第三方依赖。

## 2026-10-29 M4（原生插件系统与公共 API）复验结果

| 项目 | 要求 | 本机现状 | 检查结果 | 处理方式 |
| --- | --- | --- | --- | --- |
| 应用依赖 | 只装确有必要的新依赖 | 插件系统全部用标准库（importlib/json/sqlite3/asyncio）实现 | 无需新增依赖；`pip check` 通过 | 保持既有依赖集 |
| 数据库迁移 | 插件 KV 与运行状态表 | 迁移 v4（plugin_kv、plugin_runtime_state） | 迁移/重启不重放/表结构断言通过；真实 data/bot.db 启动时自动升级 | 追加迁移，不改历史项 |
| 插件加载安全 | 导入插件代码前完成校验 | manifest.json 先于入口模块校验（ID/版本/入口/API 版本/依赖/配置定义） | 重复 ID、路径穿越式 ID、非法入口、API 不兼容、依赖缺失、模块执行失败均有测试并明确报告 | 不自动下载/安装 |
| 生命周期语义 | 加载≠启用；失败回滚；停用撤销 | DISABLED→ENABLED 状态机 + 启用回滚 + 停用清理（命令/处理器/工具/任务）+ 重复启停幂等 | 全部有单元测试；后台任务取消与崩溃观察有测试 | 卸载=撤销注册，不做模块热卸载 |
| 会话串行与隔离 | 插件不破坏原流水线 | 插件阶段位于会话锁内、记忆命令之前；回调异常记日志不中断 | 命令异常→错误回复；处理器异常→跳过继续；普通消息回归测试通过 | 未消费消息走原链路 |
| KV 与命名空间 | 宿主绑定命名空间 | PluginKV 接口无命名空间参数；namespace=插件 ID | 类型/限额/隔离/跨重启持久测试通过 | 与记忆表互不相干 |
| 管理入口 | 仅主人 | /plugin list/status/enable/disable 私聊命令（OWNER 角色） | 非主人拒绝有测试；运行状态行优先于配置有测试（含重启） | 持久化在 plugin_runtime_state |
| 自动化测试 | 全量回归 | 232 项（138 基线 + 94 新增：清单/加载/宿主/KV/工具/管理 + 3 项端到端） | 连续三次全绿 | 先替身后真实 |
| M4 真实验收 | 授权私聊内命令闭环 | bot 3666167977 ↔ 主人 844281600 | /hello、/counter、/plugin 停用与重启用、普通聊天回归（详见本轮报告） | 通过 |

本轮无第三方代码复制（`docs/upstream-sources.md` 无需变更）；ego-lite 仍属 M5。

## 2026-10-29 M5（统一工具执行循环 + ego-lite 网页任务）复验结果

| 项目 | 要求 | 本机现状 | 检查结果 | 处理方式 |
| --- | --- | --- | --- | --- |
| DeepSeek 工具协议 | 以当前官方文档为准 | tools[]（function.name/description/parameters）→ message.tool_calls[]（id/name/arguments）→ role=tool + tool_call_id 回传；deepseek-flash 非思考模式支持；strict 为 beta 不采用 | 适配器保留 tool_calls/finish_reason/逐轮 usage；闭环经真实 API 验证 | 沿用 httpx 非流式路径 |
| ego-lite 接入 | 读本机 skill、按实测接口接入 | ego-browser CLI 0.6.1.13（skill 指南 v2.1.0）；`ego-browser nodejs` 子进程执行脚本；console.log 输出在 stderr；TaskSpace 按名跨进程恢复（实测 space id 一致） | 固定脚本模板 + JSON 参数头经 stdin 注入；stderr 解析 `__EGO_RESULT__` 行；参数是数据不是代码 | 自研桥接，不引入 Playwright |
| 搜索引擎 | 实际可用、结果真实 | Bing 固定搜索流程实测返回 6 条结果；重定向参数（bing.com/ck/a?u=a1<base64>）已解码为真实链接 | 搜索/打开/越界拒绝/finish 清理全流程实测通过 | 不暗中替换搜索服务 |
| 应用依赖 | 只装必要依赖 | 全部标准库实现（asyncio/json/subprocess/ipaddress） | 无新增依赖；`pip check` 通过 | 保持既有依赖集 |
| 工具执行语义 | 权限/校验/超时/取消/结果结构 | RunContext 贯穿；执行前复查注册+启停+能力；JSON 解析（不 eval）+扁平 Schema 校验；超时与取消竞速；结构保持裁剪；重复 call_id 复用 | 单测 38+16 项覆盖（执行服务/任务管理器） | M4 旧签名经 wants_context 显式兼容 |
| 工具循环 | 多轮闭环与限制 | 轮数/调用数/时限/结果长度可配；每轮计入输入预算（配对裁剪）；逐轮用量落库；空响应/非法 JSON/未知工具/服务错误均有终态 | 替身模型 16 项 + 集成 3 项（真实插件工具闭环、锁外控制路径、非主人拒绝） | pending 按会话隔离并绑定 message_id |
| 任务管理与取消 | 锁外控制、取消传导 | /task list/status/cancel 在会话锁外处理；取消经 RunContext 事件传导到循环/工具/浏览器清理；状态五态 | 集成测试：长任务持锁时 list/cancel 立即响应、取消后无下一轮请求 | 受理提示含任务 ID |
| URL 边界 | 仅公开网页 | urlguard 拒绝本机/私网/链路本地/保留 IP/内部域名/无点主机/非 HTTP/用户信息；跳转后实际地址再检 | 16 组参数化用例 + 真实 127.0.0.1 拒绝实测 | DNS 重绑定如实标注不在范围 |
| 浏览器资源 | 任务归属与清理 | 一任务一 TaskSpace 按名复用；清理钩子在取消/超时/完成/停用/退出触发；失败报"状态未知" | 替身浏览器 36 项（含取消释放锁、忙时立即失败、空间复用/换名重试） | 并发准入全局 1 |
| 自动化测试 | 全量回归 | 331 项（232 基线 + 99 新增，含验收缺陷回归） | 全绿 | 先替身后真实 |
| M5 真实验收 | 无副作用工具→ego 任务→取消→故障回归 | bot 3666167977 ↔ 主人 844281600 | 全部通过：hello_greet 真实闭环（2 轮 1 工具）；网页任务交付 883 字结论 + 4 个真实来源链接（etymonline 等）；/task cancel 后任务 CANCELLED、无迟到答复、ego 侧 companion 空间残留 0；插件停用与 DNS 故障期间模型如实降级不编造。首轮验收发现工具轮次正文丢失缺陷，已修复（正文累积）并以重测证实 | 通过（2026-10-30） |

## 来源

- [AstrBot pyproject.toml](https://github.com/AstrBotDevs/AstrBot/blob/master/pyproject.toml)：本次读取的 Python 要求为 `>=3.12`。
- [SnowLuma README](https://github.com/SnowLuma/SnowLuma)：当前发行包和运行时要求。
- [SnowLuma v2.2.1 发布页](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.2.1)：本次读取的 Windows x64 资产。
- [Windows 部署说明](https://snowluma.github.io/zh/docs/guide/deploy/windows)
- [OneBot 配置说明](https://snowluma.github.io/zh/docs/guide/configuration)
- [DeepSeek 官方文档](https://api-docs.deepseek.com/)

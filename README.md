# Deepseek Bot

一个从空仓库构建的独立 Agent 框架。主程序和原生插件接口由本项目维护，通过独立兼容层逐步复用 AstrBot 插件生态。

## 当前阶段

M1（QQ 私聊固定应答）、M2（DeepSeek 实际应答）、M3/M3.1（统一会话内核 + 基础长期记忆及修正轮）、M4（原生插件系统与公共 API）与 M5（统一工具执行循环 + ego-lite 网页任务）已实现并验收。当前运行于 deepseek 模式：授权发送者私聊可获得模型回复，并可使用显式记忆命令（记住/查看/搜索/修改/忘记），记忆跨重启有效且按会话隔离。M4 起提供原生插件系统（`companion.api` + 插件宿主）：内置 `/hello`、`/counter` 插件与主人管理命令 `/plugin`。M5 起提供统一工具执行循环（DeepSeek tool_calls 多轮闭环、权限/参数校验、超时与取消）、任务控制命令 `/task`，以及经 ego-lite 的公开网页工具（`web_search`/`web_open`/`web_finish`，仅主人）；插件 KV 与运行状态跨重启持久。消息状态、模型用量、长期记忆与插件数据持久化在 `data/bot.db`（SQLite）。启动、停止、备份与故障处理见 [运行手册](docs/runbook.md)，插件接口见[插件系统与原生 API](docs/plugin-api.md)。

尚未开始：群聊应答（M6）、AstrBot 兼容层（M7）、自主活动（M8）、长期运行管理（M9）。

源码包名暂用 `companion`，与规划阶段保持一致；产品命名与最终发布包名可以后续确定。

## 快速开始

```powershell
Set-Location 'E:\Deepseek Bot'
# 前提：SnowLuma 与机器人 QQ 实例已运行（见 docs/runbook.md「启动前检查」）
powershell -ExecutionPolicy Bypass -File scripts\windows\start_bot.ps1 -Foreground
```

配置：复制 `config/bot.example.json` 为 `config/local.json` 填入本机值；`.env` 填 `DEEPSEEK_API_KEY`（两者均被 Git 忽略）。

## 已确定的方向

- 在当前 Windows 电脑运行，尽量长期在线。
- Python 固定使用 Conda 环境 `Deepseek_bot`，版本基线为 3.12.x；先通过对应阶段的环境检查，再进入实现与联调。
- 使用用户提供的独立 QQ 账号，优先通过 SnowLuma 的 OneBot 接口接入。
- 使用 DeepSeek API；月费用预算为 100–300 元，具体执行额度以后配置。
- 网页任务使用本机 ego-lite，通过 `ego-browser` 接入。
- 自有内核、原生插件 API、AstrBot 插件兼容层分别维护。
- 记忆服务与插件系统是主要模块。
- 从空仓库开发，不以 AstrBot fork 或 AstrBot 内部插件作为项目基线。
- 入群按“机器人发现并推荐，用户决定是否加入”的边界规划。

## 仓库结构

```text
src/
  companion/
    api/                    原生插件使用的公共接口（已实现：Plugin/PluginContext/HandlerResult，API 版本 1.0）
    core/
      lifecycle/            启动、停止、资源清理（已实现：runner + worker 池 + 单实例保护）
      events/               内部事件与消息格式（已实现：InboundMessage）
      pipeline/             消息处理阶段及挂钩（已实现：筛选链、记忆命令、固定应答、Responder 协议）
      agent/                模型与工具执行循环（已实现：DeepSeek 有界上下文应答）
      sessions/             会话、上下文、并发控制（已实现：SessionKey、去重、锁、消息/用量存储）
      tools/                工具登记与统一执行服务（已实现：登记/权限/校验/超时/取消/裁剪）
      permissions/          身份、能力与范围检查（已实现：角色/能力/运行上下文）
    adapters/
      ego/                  ego-lite 浏览器接入（已实现：URL 边界 + 固定脚本桥接 + TaskSpace 生命周期）
      onebot/               SnowLuma 接入（已实现：正向 WS 客户端、重连重验证、动作结果三分）
      deepseek/             模型接入（已实现：chat completions 适配器）
      ego/                  ego-lite 接入
    plugin_system/          插件加载、配置与生命周期（已实现：清单校验、加载器、宿主、KV、运行状态）
    compat/astrbot/         AstrBot 插件接口与行为转换
    memory/                 记忆服务、范围与检索（已实现：显式记忆、范围隔离、修订链）
    persistence/            数据库、存储实现与迁移（已实现：SQLite + 迁移 v1–v4）
    core/tasks.py            任务状态与取消（M5：RUNNING/COMPLETED/FAILED/CANCELLED/TIMEOUT）
    management/             管理接口
    config/                 配置加载与校验（已实现：settings + 数据目录解析 + plugins 段）
    telemetry/              日志、错误与用量
  astrbot/                  限定范围的兼容导入入口
plugins/                    随项目维护的内置插件（已实现：hello、counter、ego_browser；memory_tools 为占位）
examples/plugins/           插件开发示例（已实现：echo 最小示例）
config/                     默认配置与示例的位置
scripts/windows/            本机运行辅助脚本（start_bot.ps1 / stop_bot.ps1）
ops/                        外部组件与运行维护约定
download/                   下载产物，只跟踪目录说明
temp/                       临时中间文件，只跟踪目录说明
tests/                      单元、契约、集成与插件兼容检查（unit 与 integration 已有实现）
docs/                       架构、模块设计与决策记录
environment.yml             Conda 基础环境定义
pyproject.toml              应用包定义与依赖清单
```

未实现的目录仍用 `.gitkeep` 保留。它们不是可导入的 Python 包，也不代表对应能力已经实现。

## 文档入口

- [架构与模块边界](docs/architecture.md)
- [记忆服务与持久化](docs/memory.md)
- [插件系统与原生 API](docs/plugin-api.md)
- [AstrBot 兼容范围](docs/compatibility.md)
- [已确认的项目基线](docs/decisions/0001-project-baseline.md)
- [Conda 环境与前置检查决策](docs/decisions/0002-conda-environment.md)
- [Python 与 Conda 环境约定](docs/development-environment.md)
- [环境调查与验证记录](docs/environment-audit.md)
- [可复制的 QQ 应答交接提示词](docs/agent-handoff.md)
- [M3–M9 后续能力交接提示词](docs/agent-handoff-post-m2.md)
- [上游来源与代码复用记录](docs/upstream-sources.md)
- [当前运行状态与后续检查](docs/runbook.md)

## 数据与配置

真实密钥、账号配置、聊天记录、数据库、第三方插件下载及浏览产物不进入 Git。长期运行状态与数据库目录后续由配置指定，放在源码目录之外。

根目录另提供 [download/](download/README.md) 保存下载产物，[temp/](temp/README.md) 保存临时中间文件。两个目录只跟踪各自的 `README.md`，实际文件与任务子目录由 Git 忽略。需要长期保留的资料另行归档；当前没有下载逻辑或自动清理实现。

`.env.example` 列出配置变量约定，不包含可用密钥；加载实现在 `src/companion/config/settings.py`（pydantic 校验，配置错误明确报告）。

`environment.yml` 只定义 Python 3.12 与 pip；应用依赖由 `pyproject.toml` 定义并安装到同一 Conda 环境（见 [环境约定](docs/development-environment.md)）。后续依赖工具必须使用已有 Conda 环境，不能默认另建 `.venv`。

## 尚待讨论的事项

- 第一批要兼容的 AstrBot 插件及其版本。
- 兼容层的 AstrBot 基线版本或提交。
- 本地管理界面的范围。
- 群成员调用网页工具的权限。
- 嵌入模型、检索后端及具体记忆策略。
- 人格、发言时机与自主活动策略。
- 项目发布许可，以及实际复用代码后的许可处理。

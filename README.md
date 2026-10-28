# Deepseek Bot

一个从空仓库构建的独立 Agent 框架。主程序和原生插件接口由本项目维护，通过独立兼容层逐步复用 AstrBot 插件生态。

## 当前阶段

当前只完成目录、模块职责和设计文档，没有应用代码、依赖安装、数据库或可执行入口。现在不能启动机器人，也没有接入 QQ 或调用收费 API。

源码包名暂用 `companion`，与规划阶段保持一致；产品命名与最终发布包名可以后续确定。

## 已确定的方向

- 在当前 Windows 电脑运行，尽量长期在线。
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
    api/                    原生插件使用的公共接口
    core/
      lifecycle/            启动、停止、资源清理
      events/               内部事件与消息格式
      pipeline/             消息处理阶段及挂钩
      agent/                模型与工具执行循环
      sessions/             会话、上下文、并发控制
      tools/                工具登记与执行入口
      permissions/          身份、能力与范围检查
    adapters/
      onebot/               SnowLuma 接入
      deepseek/             模型接入
      ego/                  ego-lite 接入
    plugin_system/          插件加载、配置与生命周期
    compat/astrbot/         AstrBot 插件接口与行为转换
    memory/                 记忆服务、范围与检索
    persistence/            数据库、存储实现与迁移
    management/             管理接口
    config/                 配置加载与校验
    telemetry/              日志、错误与用量
  astrbot/                  限定范围的兼容导入入口
plugins/                    随项目维护的内置插件
examples/plugins/           后续插件示例
config/                     默认配置与示例的位置
scripts/windows/            后续本机运行辅助脚本
ops/                        外部组件与运行维护约定
tests/                      单元、契约、集成与插件兼容检查
docs/                       架构、模块设计与决策记录
```

空目录使用 `.gitkeep` 保留。它们不是可导入的 Python 包，也不代表对应能力已经实现。

## 文档入口

- [架构与模块边界](docs/architecture.md)
- [记忆服务与持久化](docs/memory.md)
- [插件系统与原生 API](docs/plugin-api.md)
- [AstrBot 兼容范围](docs/compatibility.md)
- [已确认的项目基线](docs/decisions/0001-project-baseline.md)
- [上游来源与代码复用记录](docs/upstream-sources.md)
- [当前运行状态与后续检查](docs/runbook.md)

## 数据与配置

真实密钥、账号配置、聊天记录、数据库、第三方插件下载及浏览产物不进入 Git。实际运行数据目录后续由配置指定，放在源码目录之外。

`.env.example` 只列出候选配置变量，不包含可用密钥，当前也没有配置加载实现。

`pyproject.toml`、`uv.lock`、Python 入口、数据库迁移和 Windows 脚本会在对应实现阶段添加，避免把占位清单误当成可用环境。

## 尚待讨论的事项

- 第一批要兼容的 AstrBot 插件及其版本。
- 兼容层的 AstrBot 基线版本或提交。
- 本地管理界面的范围。
- 群成员调用网页工具的权限。
- 嵌入模型、检索后端及具体记忆策略。
- 人格、发言时机与自主活动策略。
- 项目发布许可，以及实际复用代码后的许可处理。

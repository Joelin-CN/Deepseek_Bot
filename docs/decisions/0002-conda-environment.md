# 0002：固定 Conda 环境与环境前置检查

日期：2026-10-28

状态：用户明确要求，基础环境已创建并验证。

## 决定

- 项目 Python 环境固定命名为 `Deepseek_bot`，使用 Python 3.12.x。
- 复用本机 Anaconda，在独立环境内运行项目，不使用 base 或其他项目环境。
- 本机解释器为 `E:\Softwares\Anaconda\envs\Deepseek_bot\python.exe`。
- Agent 的每次 Python 命令明确指向此环境，不能依赖前一次工具 shell 的激活状态。
- 先调研路线要求、检查本机、识别缺口、配置环境、验证环境，再开始对应里程碑实现和联调。

## 本轮边界

本轮新增授权是创建基础 Conda 环境并更新环境文档、交接提示词。没有开始应用实现，没有安装 SnowLuma、应用依赖或登录 QQ。

此前“仅骨架、没有运行环境”的阶段状态已由本记录更新；[0001](0001-project-baseline.md) 中的独立框架、插件兼容和接入路线等方向继续有效。

## 文档

- [环境使用约定](../development-environment.md)
- [环境调查与验证记录](../environment-audit.md)
- [QQ 应答交接提示词](../agent-handoff.md)

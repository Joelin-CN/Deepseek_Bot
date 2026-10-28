# 外部组件与运行维护

本目录约定 SnowLuma、QQ、ego-lite 等外部组件的管理方式。实际启停步骤见 [运行手册](../docs/runbook.md)。

## 已联调验证的版本组合（2026-10-28）

| 组件 | 版本 | 位置 |
| --- | --- | --- |
| QQ NT | 基础 9.9.29-47354 + 热更新 9.9.36-53644 | `C:\Program Files\Tencent\QQNT`（两个实例：个人 QQ + 机器人 QQ） |
| SnowLuma | v2.2.1 Windows 完整版（自带 Node） | `E:\Softwares\SnowLuma`（仓库之外） |
| OneBot | 正向 WS 127.0.0.1:3001，Universal，array 格式，Token 鉴权 | 配置在 SnowLuma `config\onebot_3666167977.json` |
| DeepSeek | 官方 API，模型 deepseek-flash | Key 在仓库根 `.env`（Git 忽略） |
| Python | Conda `Deepseek_bot`，3.12.16 | `E:\Softwares\Anaconda\envs\Deepseek_bot` |

未验证的升级（QQ 自动更新、SnowLuma 新版、依赖升级）不视为已兼容；升级后按 [环境调查记录](../docs/environment-audit.md) 重新核对再联调。

## 管理边界

- 外部程序与登录态放源码仓库之外；SnowLuma 的配置、WebUI 密码和 QQ 登录态都在 `E:\Softwares\SnowLuma`，备份时整个目录处理，不提交 Git。
- 本项目脚本只管理本项目创建的进程（机器人 Python 进程），不批量结束 QQ、SnowLuma 或浏览器。
- SnowLuma 的 `hookAutoLoad` 保持 false：只对机器人 QQ 实例手动注入，避免接入个人 QQ 账号。
- 机器人 QQ 掉线/重启后：WebUI（127.0.0.1:5099）确认该实例 online，必要时手动 Load。

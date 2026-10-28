# Windows 运行辅助脚本

- `start_bot.ps1`：用 Conda `Deepseek_bot` 环境启动机器人（`python -m companion`）。
  - `-Foreground`：前台运行，Ctrl+C 优雅退出（推荐日常使用）。
  - 默认后台运行，PID 记录在 `runtime\bot.pid`，日志写入 `logs\bot.log`。
- `stop_bot.ps1`：只结束 `runtime\bot.pid` 记录的单个进程；PID 对应进程名不是 python 时拒绝执行。这是强制结束；优雅退出请用前台模式。

脚本只管理本项目创建的进程，不批量终止 QQ、SnowLuma、浏览器或其他同名进程。SnowLuma 与机器人 QQ 实例需要另行保持运行（见 [ops](../../ops/README.md) 与 [runbook](../../docs/runbook.md)）。

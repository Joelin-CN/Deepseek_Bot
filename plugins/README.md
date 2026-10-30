# 本项目维护的内置插件

本目录存放随项目维护的内置插件，插件目录 = `manifest.json` + 入口 `.py`。

已实现（M4）：

- `hello/`：`/hello` 命令，展示运行上下文信息；登记一个工具描述。
- `counter/`：`/counter` 计数命令，KV 持久化跨重启；含仅写日志的心跳后台任务。
- `ego_browser/`（M5）：网页任务工具 `web_search`/`web_open`/`web_finish`，仅主人、
  仅公开网页，底层经 `src/companion/adapters/ego` 桥接本机 ego-lite。

保留占位（后续里程碑）：

- `memory_tools/`：记忆工具的插件入口位置（记忆算法专项，后续接入）。

启用方式：在 `config/local.json` 的 `plugins.instances` 中按 ID 启用，或运行时
用主人私聊命令 `/plugin enable <ID>`。运行状态持久化在数据库，优先于配置文件。

运行时下载的第三方插件放在独立数据目录（`plugins.extra_roots` 指向），不直接
混入此目录。原生 API 文档见 `docs/plugin-api.md`。

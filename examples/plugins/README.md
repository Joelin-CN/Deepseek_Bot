# 开发示例插件

这里的插件用于开发与调试，不随项目作为内置能力发布。

## echo

最小可运行示例：`manifest.json` + 单文件入口 `main.py`，注册 `/echo` 命令。
加载方式：在 `config/local.json` 的 `plugins.extra_roots` 加入 `examples/plugins`，
并在 `plugins.instances` 中启用 `example.echo`（见 `echo/main.py` 顶部注释）。

## 编写新插件

1. 新建目录，包含 `manifest.json` 与入口 `.py` 文件（entry 形如 `main:类名`）；
2. 入口类继承 `companion.api.Plugin`，在 `on_enable(ctx)` 里注册命令/处理器/工具/任务；
3. 公共接口、生命周期语义、KV 与配置限制见 `docs/plugin-api.md`；
4. 更完整的内置示例见仓库根 `plugins/hello` 与 `plugins/counter`。

注意：示例与内置插件同为可信本机代码（同进程运行），不要加载来历不明的插件。

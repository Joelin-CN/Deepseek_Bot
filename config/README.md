# 配置文件位置

`examples/` 保留给后续带加载器的多场景示例。当前配置格式与校验由 `src/companion/config/settings.py`（pydantic）定义：`bot.example.json` 是模板，复制为 `local.json` 填入本机值后生效；配置错误在启动时逐字段报告。

机器路径、QQ 身份和实际运行参数放在本机配置（`local.*`，已忽略）中；真实密钥（如 DeepSeek API Key）通过根目录 `.env` 提供，不写入 JSON 配置，也不提交到 Git。

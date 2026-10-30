"""Configuration models and loading.

Machine-local values (WebSocket address, token, QQ numbers) live in
``config/local.json`` which is git-ignored; ``config/bot.example.json`` is the
tracked template. Secrets such as the DeepSeek API key come from the ``.env``
file or process environment and must never be committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from companion.api.types import valid_plugin_id

DEFAULT_CONFIG_PATH = Path("config") / "local.json"
EXAMPLE_CONFIG_PATH = Path("config") / "bot.example.json"


class ConfigError(Exception):
    """配置无法加载或校验失败，消息直接面向使用者。"""


class OneBotSettings(BaseModel):
    ws_url: str = "ws://127.0.0.1:3001/"
    access_token: SecretStr
    self_id: int = Field(gt=10000, description="机器人 QQ 号（uin）")
    connect_timeout: float = Field(default=10.0, gt=0)
    action_timeout: float = Field(default=15.0, gt=0)
    reconnect_min_delay: float = Field(default=1.0, gt=0)
    reconnect_max_delay: float = Field(default=30.0, gt=0)

    @field_validator("ws_url")
    @classmethod
    def _check_ws_url(cls, value: str) -> str:
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url 必须以 ws:// 或 wss:// 开头")
        return value


class BotSettings(BaseModel):
    owner_qq: int | None = Field(default=None, description="主人 QQ 号；管理能力只授予此账号")
    allowed_private_senders: list[int] = Field(min_length=1, description="允许触发回复的私聊发送者 QQ 号")
    reply_mode: Literal["fixed", "deepseek"] = "fixed"
    fixed_reply_text: str = "收到，QQ 链路已连通。"
    dedup_cache_size: int = Field(default=2048, ge=16)
    log_level: str = "INFO"
    max_handlers: int = Field(default=4, ge=1, le=32, description="事件处理 worker 数量上限")
    event_queue_size: int = Field(default=1000, ge=100, description="事件队列容量；满时丢弃新事件")
    data_dir: str | None = Field(default=None, description="数据目录；缺省为配置文件旁的 data/")


class DeepSeekSettings(BaseModel):
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    timeout: float = Field(default=60.0, gt=0)
    max_tokens: int = Field(default=1024, ge=1, le=8192, description="单次回复输出 token 上限")
    max_input_tokens: int = Field(default=8000, ge=256, description="模型输入总预算：系统提示+记忆+历史+当前消息")
    max_history_messages: int = Field(default=20, ge=0, description="短期上下文消息条数上限，0 表示不带上下文")
    max_history_tokens: int = Field(default=2000, ge=0, description="短期上下文估算 token 上限")
    max_reply_chars: int = Field(default=3000, ge=100, description="发送前回复长度截断保护")
    model_concurrency: int = Field(default=4, ge=1, description="模型并发调用上限")
    memory_context_items: int = Field(default=8, ge=0, description="注入上下文的记忆条数上限，0 表示不注入")
    enable_thinking: bool = Field(default=False, description="是否开启思考模式（更慢、消耗更多 token）")
    system_prompt: str = "你是一个友好的 QQ 私聊机器人助手，用简洁的中文回答。"


class PluginInstanceSettings(BaseModel):
    """单个插件的配置段（plugins.instances.<插件ID>）。

    enabled 只决定**首次启动**（无运行状态行时）是否启用；显式
    /plugin enable|disable 写入数据库的运行状态行优先于本配置。
    """

    enabled: bool = Field(default=False)
    config: dict[str, Any] = Field(default_factory=dict, description="覆盖清单缺省值的配置项；键与类型在启用时按清单校验")

    @field_validator("config")
    @classmethod
    def _check_config_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not isinstance(key, str) or not key or len(key) > 64:
                raise ValueError(f"配置键非法：{key!r}（非空字符串，≤64 字符）")
        return value


class PluginsSettings(BaseModel):
    """插件系统配置（plugins 段）。"""

    extra_roots: list[str] = Field(default_factory=list, description="外部插件目录列表；相对路径按仓库根解析。与内置 plugins/ 同等信任级别")
    instances: dict[str, PluginInstanceSettings] = Field(default_factory=dict, description="按插件 ID 索引的启用状态与配置")

    @field_validator("instances")
    @classmethod
    def _check_instance_ids(cls, value: dict[str, PluginInstanceSettings]) -> dict[str, PluginInstanceSettings]:
        for plugin_id in value:
            if not valid_plugin_id(plugin_id):
                raise ValueError(
                    f"插件 ID 非法：{plugin_id!r}（小写段式标识，如 builtin.hello）"
                )
        return value


class ToolSettings(BaseModel):
    """M5 统一工具执行循环的限制。"""

    enable: bool = Field(default=True, description="是否启用模型工具调用循环；False 时纯聊天直答")
    max_model_rounds: int = Field(default=5, ge=1, le=20, description="单个任务的模型请求轮数上限")
    max_tool_calls: int = Field(default=8, ge=1, le=32, description="单个任务的工具执行总次数上限")
    tool_timeout: float = Field(default=60.0, ge=5, le=300, description="单次工具执行超时（秒）")
    task_timeout: float = Field(default=300.0, ge=10, le=1800, description="单任务总时限（秒）")
    max_tool_result_chars: int = Field(default=6000, ge=200, le=20000, description="单个工具结果序列化字符上限")
    accept_notice: bool = Field(default=True, description="进入工具循环时是否发送一次受理提示（含任务 ID）")


class Settings(BaseModel):
    onebot: OneBotSettings
    bot: BotSettings
    deepseek: DeepSeekSettings = DeepSeekSettings()
    tools: ToolSettings = ToolSettings()
    plugins: PluginsSettings = PluginsSettings()
    data_dir_resolved: Path | None = None  # load_settings 填充；相对路径基于配置文件位置解析
    plugin_roots_resolved: list[Path] = Field(default_factory=list)  # load_settings 填充：内置 plugins/ 在前，extra_roots 在后


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate the bot configuration from a local JSON file."""
    config_path = Path(path or os.environ.get("COMPANION_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.exists():
        raise ConfigError(
            f"未找到配置文件 {config_path}。请复制 {EXAMPLE_CONFIG_PATH} 为 {config_path}，"
            "填入本机的 OneBot 地址、access_token、机器人 QQ 号和授权发送者列表。"
        )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 {config_path} 不是合法 JSON：{exc}") from exc
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        detail = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"配置文件 {config_path} 校验失败：\n{detail}") from exc
    if settings.bot.reply_mode == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ConfigError(
            "bot.reply_mode 为 deepseek，但未设置 DEEPSEEK_API_KEY。"
            "请在仓库根目录的 .env 文件中填写（参考 .env.example），不要写入受 Git 跟踪的文件。"
        )
    raw_dir = os.environ.get("COMPANION_DATA_DIR", "").strip() or settings.bot.data_dir
    if raw_dir:
        data_path = Path(raw_dir).expanduser()
        if not data_path.is_absolute():
            data_path = config_path.parent.parent / data_path
    else:
        data_path = config_path.parent.parent / "data"
    settings.data_dir_resolved = data_path.resolve()
    repo_root = config_path.parent.parent
    plugin_roots: list[Path] = [(repo_root / "plugins").resolve()]
    for raw_root in settings.plugins.extra_roots:
        root_path = Path(raw_root).expanduser()
        if not root_path.is_absolute():
            root_path = repo_root / root_path
        plugin_roots.append(root_path.resolve())
    settings.plugin_roots_resolved = plugin_roots
    return settings

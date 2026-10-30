"""DeepSeek adapter: official chat completions API over httpx."""

from companion.adapters.deepseek.client import ChatResult, DeepSeekClient, DeepSeekError, ToolCallData

__all__ = ["ChatResult", "DeepSeekClient", "DeepSeekError", "ToolCallData"]

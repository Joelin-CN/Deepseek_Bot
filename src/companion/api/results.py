"""插件处理器返回结果。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HandlerResult:
    """消息处理器/命令处理器的统一返回语义。

    - ``consumed=True, text=文本``：消费该消息并回复此文本；
    - ``consumed=True, text=None``：消费但不回复（消息记录为 consumed，不进入对话历史）；
    - ``consumed=False``：不消费，继续交给后续处理器/记忆命令/模型。

    处理器也可以直接返回 ``None``（等价于不消费）或纯字符串（等价于消
    费并回复），宿主会归一化。
    """

    consumed: bool
    text: str | None = None

    @classmethod
    def reply(cls, text: str) -> "HandlerResult":
        if not isinstance(text, str):
            raise TypeError(f"回复必须是 str，收到 {type(text).__name__}")
        return cls(consumed=True, text=text)

    @classmethod
    def consume(cls) -> "HandlerResult":
        return cls(consumed=True, text=None)

    @classmethod
    def ignore(cls) -> "HandlerResult":
        return cls(consumed=False, text=None)


def normalize_result(result: "HandlerResult | str | None") -> HandlerResult | None:
    """把处理器返回值归一化为 HandlerResult；None 表示不消费。"""
    if result is None:
        return None
    if isinstance(result, str):
        return HandlerResult.reply(result)
    if isinstance(result, HandlerResult):
        return result
    raise TypeError(f"处理器返回值必须是 str/HandlerResult/None，收到 {type(result).__name__}")

"""短期上下文的 token 估算与裁剪。

估算是保守的字符级近似（CJK 计 1、其他约 4 字符 1 token），用于限制
上下文规模，不用作计费依据。
"""

from __future__ import annotations

from typing import Sequence


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def clip_to_token_budget(text: str, budget: int) -> str:
    """截取前缀使其估算 token 不超过 budget（估算随长度单调，二分定位）。"""
    if estimate_tokens(text) <= budget:
        return text
    if budget <= 0:
        return ""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _message_tokens(message: dict) -> int:
    content = message.get("content") or ""
    return estimate_tokens(content) + 4  # 角色/结构开销的粗略常数


def trim_history(
    history: Sequence[dict], max_messages: int, max_tokens: int
) -> list[dict]:
    """从最新往回保留，同时受条数与估算 token 上限约束。

    保证结果不以 assistant 消息开头（保持问/答配对完整，便于后续
    工具调用消息扩展时维持 tool_call 与结果的对应关系）。
    """
    kept: list[dict] = []
    total = 0
    if max_messages <= 0:
        return []
    for message in reversed(history):
        if len(kept) >= max_messages:
            break
        cost = _message_tokens(message)
        if max_tokens > 0 and kept and total + cost > max_tokens:
            break
        kept.append(message)
        total += cost
    kept.reverse()
    while kept and kept[0].get("role") == "assistant":
        kept.pop(0)
    return kept

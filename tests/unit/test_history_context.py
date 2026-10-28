"""token 估算与短期上下文裁剪。"""

from companion.core.agent.context import estimate_tokens, trim_history


def test_cjk_counts_about_one_token_per_char():
    assert estimate_tokens("我喜欢蓝色") == 5


def test_ascii_counts_about_four_chars_per_token():
    assert estimate_tokens("abcdefgh") == 2  # 8/4
    assert estimate_tokens("abc") == 1  # (3+3)//4


def test_mixed_text():
    tokens = estimate_tokens("你好abc")
    assert tokens == 2 + 1


def test_trim_by_message_count():
    history = [
        {"role": "user", "content": "问1"},
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": "问2"},
        {"role": "assistant", "content": "答2"},
        {"role": "user", "content": "问3"},
        {"role": "assistant", "content": "答3"},
    ]
    kept = trim_history(history, max_messages=2, max_tokens=0)
    assert [m["content"] for m in kept] == ["问3", "答3"]


def test_trim_by_token_budget_keeps_recent():
    history = [
        {"role": "user", "content": "很长的开头" * 50},
        {"role": "assistant", "content": "旧回复" * 50},
        {"role": "user", "content": "短问"},
        {"role": "assistant", "content": "短答"},
    ]
    kept = trim_history(history, max_messages=100, max_tokens=30)
    assert kept[-1]["content"] == "短答"
    assert len(kept) < len(history)


def test_trim_keeps_user_first_pairing():
    history = [
        {"role": "user", "content": "问1"},
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": "问2"},
        {"role": "assistant", "content": "答2"},
    ]
    kept = trim_history(history, max_messages=3, max_tokens=0)
    # 条数上限 3 时从最新往回取 3 条，去掉最旧的 user，结果以 user 开头
    assert kept[0]["role"] == "user"
    assert len(kept) <= 3


def test_trim_with_zero_history_config_returns_empty():
    assert trim_history([{"role": "user", "content": "x"}], max_messages=0, max_tokens=0) == []

from companion.adapters.onebot.segments import text_segment


def test_text_segment_structure():
    assert text_segment("你好") == [{"type": "text", "data": {"text": "你好"}}]


def test_text_segment_preserves_punctuation():
    assert text_segment("收到，QQ 链路已连通。") == [
        {"type": "text", "data": {"text": "收到，QQ 链路已连通。"}}
    ]

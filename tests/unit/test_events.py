from companion.adapters.onebot.events import extract_text, parse_private_message


def _event(**overrides):
    base = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 12345,
        "user_id": 844281600,
        "self_id": 3666167977,
        "time": 1793182844,
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "raw_message": "你好",
    }
    base.update(overrides)
    return base


def test_parse_array_format_message():
    message = parse_private_message(_event())
    assert message is not None
    assert message.message_id == 12345
    assert message.user_id == 844281600
    assert message.self_id == 3666167977
    assert message.sub_type == "friend"
    assert message.text == "你好"


def test_parse_string_format_message_tolerated():
    message = parse_private_message(_event(message="纯文本消息"))
    assert message is not None
    assert message.text == "纯文本消息"


def test_parse_missing_required_field_returns_none():
    broken = _event()
    del broken["user_id"]
    assert parse_private_message(broken) is None


def test_extract_text_joins_only_text_segments():
    mixed = [
        {"type": "image", "data": {"file": "x.jpg"}},
        {"type": "text", "data": {"text": "前"}},
        {"type": "face", "data": {"id": "1"}},
        {"type": "text", "data": {"text": "后"}},
    ]
    assert extract_text(mixed) == "前后"

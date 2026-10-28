from companion.adapters.onebot.events import extract_text, parse_message

SELF_ID = 3666167977
SENDER = 844281600
GROUP_ID = 4321


def _event(**overrides):
    base = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 12345,
        "user_id": SENDER,
        "self_id": SELF_ID,
        "time": 1793182844,
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "raw_message": "你好",
        "sender": {"user_id": SENDER, "nickname": "Zero"},
    }
    base.update(overrides)
    return base


def test_parse_private_message_array():
    message = parse_message(_event())
    assert message is not None
    assert message.message_id == 12345
    assert message.sender_id == SENDER
    assert message.session.platform == "qq"
    assert message.session.bot_id == SELF_ID
    assert message.session.chat_type == "private"
    assert message.session.chat_target == SENDER
    assert message.sub_type == "friend"
    assert message.text == "你好"
    assert message.sender_nickname == "Zero"


def test_parse_group_message_records_sender_separately():
    message = parse_message(
        _event(
            message_type="group",
            sub_type="normal",
            group_id=GROUP_ID,
            message_id=777,
            user_id=999,
            sender={"user_id": 999, "card": "群名片", "nickname": "昵称"},
        )
    )
    assert message is not None
    assert message.session.chat_type == "group"
    assert message.session.chat_target == GROUP_ID  # 会话目标是群，不是发送者
    assert message.sender_id == 999
    assert message.sender_nickname == "群名片"


def test_private_and_group_sessions_are_distinct_keys():
    private = parse_message(_event())
    group = parse_message(_event(message_type="group", group_id=GROUP_ID))
    assert private.session != group.session
    assert private.session.as_scope() == f"qq/{SELF_ID}/private/{SENDER}"
    assert group.session.as_scope() == f"qq/{SELF_ID}/group/{GROUP_ID}"


def test_parse_string_format_message_tolerated():
    message = parse_message(_event(message="纯文本消息"))
    assert message is not None
    assert message.text == "纯文本消息"


def test_parse_missing_required_field_returns_none():
    broken = _event()
    del broken["user_id"]
    assert parse_message(broken) is None


def test_parse_unknown_message_type_returns_none():
    assert parse_message(_event(message_type="guild")) is None


def test_extract_text_joins_only_text_segments():
    mixed = [
        {"type": "image", "data": {"file": "x.jpg"}},
        {"type": "text", "data": {"text": "前"}},
        {"type": "face", "data": {"id": "1"}},
        {"type": "text", "data": {"text": "后"}},
    ]
    assert extract_text(mixed) == "前后"

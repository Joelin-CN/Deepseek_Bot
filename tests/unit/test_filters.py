from companion.core.pipeline import FilterChain

SELF_ID = 3666167977
ALLOWED = 844281600


def make_chain() -> FilterChain:
    return FilterChain(self_id=SELF_ID, allowed_senders={ALLOWED})


def _event(**overrides):
    base = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 1,
        "user_id": ALLOWED,
        "self_id": SELF_ID,
    }
    base.update(overrides)
    return base


def test_accepts_authorized_private_friend_message():
    assert make_chain().accepts(_event())


def test_rejects_group_message():
    assert not make_chain().accepts(_event(message_type="group", group_id=123))


def test_rejects_unauthorized_sender():
    assert not make_chain().accepts(_event(user_id=999999999))


def test_rejects_self_message():
    assert not make_chain().accepts(_event(user_id=SELF_ID))


def test_rejects_wrong_self_id():
    assert not make_chain().accepts(_event(self_id=1111111111))


def test_rejects_non_message_events():
    assert not make_chain().accepts({"post_type": "meta_event", "meta_event_type": "heartbeat"})
    assert not make_chain().accepts({"post_type": "notice", "notice_type": "friend_add"})
    assert not make_chain().accepts({"post_type": "request", "request_type": "friend"})


def test_rejects_group_temp_session_by_default():
    assert not make_chain().accepts(_event(sub_type="group"))


def test_classify_returns_reason():
    _, reason = make_chain().classify({"post_type": "meta_event"})
    assert "post_type" in reason

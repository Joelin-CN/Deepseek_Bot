"""消息状态机与用量落库。"""

from companion.core.events import InboundMessage
from companion.core.sessions.identity import SessionKey
from companion.core.sessions.store import MessageStore, UsageStore
from companion.persistence import Database

SELF_ID = 3666167977
SENDER = 844281600


def _message(text="你好", message_id=101):
    return InboundMessage(
        message_id=message_id,
        session=SessionKey("qq", SELF_ID, "private", SENDER),
        sender_id=SENDER,
        sender_nickname="Zero",
        sub_type="friend",
        text=text,
        time=1793182844,
    )


async def test_inbound_recorded_as_received(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MessageStore(db)
    try:
        row_id = await store.record_inbound(_message("记住：我喜欢蓝色"))
        record = await store.get(row_id)
        assert record is not None
        assert record["status"] == "received"
        assert record["session_key"] == f"qq/{SELF_ID}/private/{SENDER}"
        assert record["platform_message_id"] == 101
        assert record["content_preview"] == "记住：我喜欢蓝色"
    finally:
        await db.close()


async def test_status_chain_generated_then_delivered(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MessageStore(db)
    try:
        row_id = await store.record_inbound(_message())
        await store.mark_generated(row_id, "这是模型回复")
        record = await store.get(row_id)
        assert record["status"] == "model_generated"
        assert record["reply_preview"] == "这是模型回复"

        await store.mark_delivery(row_id, "sent_ok", delivery_message_id=987654)
        record = await store.get(row_id)
        assert record["status"] == "sent_ok"
        assert record["delivery_message_id"] == 987654

        await store.mark_delivery(row_id, "send_unknown")
        record = await store.get(row_id)
        assert record["status"] == "send_unknown"
    finally:
        await db.close()


async def test_failed_and_unknown_are_distinct_statuses(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    store = MessageStore(db)
    try:
        ok_row = await store.record_inbound(_message("a", 1))
        fail_row = await store.record_inbound(_message("b", 2))
        unknown_row = await store.record_inbound(_message("c", 3))
        await store.mark_delivery(ok_row, "sent_ok", 1)
        await store.mark_delivery(fail_row, "send_failed")
        await store.mark_delivery(unknown_row, "send_unknown")
        statuses = {
            (await store.get(row_id))["status"]
            for row_id in (ok_row, fail_row, unknown_row)
        }
        assert statuses == {"sent_ok", "send_failed", "send_unknown"}
    finally:
        await db.close()


async def test_usage_recorded(tmp_path):
    db = await Database.open(tmp_path / "bot.db")
    usage = UsageStore(db)
    try:
        await usage.record(
            session_key="qq/1/private/2", actor_id=2, source="chat", model="deepseek-flash",
            prompt_tokens=24, completion_tokens=34, total_tokens=58,
        )
        row = await db.fetchone("SELECT session_key, model, total_tokens FROM usage")
        assert row == ("qq/1/private/2", "deepseek-flash", 58)
    finally:
        await db.close()

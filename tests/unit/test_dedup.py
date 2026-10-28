from companion.core.sessions import DedupCache, SessionLocks


def test_first_sight_is_not_duplicate():
    dedup = DedupCache(maxsize=4)
    assert dedup.seen_before(1) is False


def test_repeat_is_duplicate():
    dedup = DedupCache(maxsize=4)
    dedup.seen_before(1)
    assert dedup.seen_before(1) is True
    assert dedup.seen_before(1) is True


def test_cache_is_bounded_and_evicts_oldest():
    dedup = DedupCache(maxsize=2)
    dedup.seen_before(1)
    dedup.seen_before(2)
    dedup.seen_before(3)  # 挤掉 1
    assert dedup.seen_before(1) is False
    assert dedup.seen_before(3) is True


async def test_session_locks_same_key_serializes():
    locks = SessionLocks()
    assert locks.for_key(1) is locks.for_key(1)
    assert locks.for_key(1) is not locks.for_key(2)

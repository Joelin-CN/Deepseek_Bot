from companion.core.permissions import Capability, PermissionDenied, Role, RunContext, actor_for
from companion.core.sessions.identity import SessionKey

OWNER = 844281600
MEMBER = 111111
STRANGER = 999999999


def _session(target=OWNER):
    return SessionKey(platform="qq", bot_id=3666167977, chat_type="private", chat_target=target)


def test_owner_role_from_config():
    actor = actor_for("qq", OWNER, "Zero", owner_id=OWNER, allowed_senders={OWNER, MEMBER})
    assert actor.role is Role.OWNER
    assert Capability.ADMIN_STATUS in actor.capabilities


def test_member_role_from_allowlist():
    actor = actor_for("qq", MEMBER, "M", owner_id=OWNER, allowed_senders={OWNER, MEMBER})
    assert actor.role is Role.MEMBER
    assert Capability.CHAT in actor.capabilities
    assert Capability.MEMORY_MANAGE in actor.capabilities
    assert Capability.ADMIN_STATUS not in actor.capabilities


def test_unknown_role_not_in_any_list():
    actor = actor_for("qq", STRANGER, "X", owner_id=OWNER, allowed_senders={OWNER})
    assert actor.role is Role.UNKNOWN
    assert not actor.capabilities


def test_role_cannot_be_claimed_by_nickname():
    # 昵称自称主人不改变身份：身份只来自已验证的平台 ID 与本地配置
    actor = actor_for("qq", STRANGER, "主人", owner_id=OWNER, allowed_senders=set())
    assert actor.role is Role.UNKNOWN


def test_run_context_require_raises_when_missing():
    actor = actor_for("qq", STRANGER, "X", owner_id=OWNER, allowed_senders=set())
    ctx = RunContext(session=_session(), actor=actor)
    try:
        ctx.require(Capability.CHAT)
    except PermissionDenied as exc:
        assert "CHAT" in str(exc) or "chat" in str(exc)
    else:
        raise AssertionError("应当抛出 PermissionDenied")


def test_run_context_require_passes_for_owner():
    actor = actor_for("qq", OWNER, "Zero", owner_id=OWNER, allowed_senders={OWNER})
    ctx = RunContext(session=_session(), actor=actor)
    for capability in Capability:
        ctx.require(capability)  # 全部能力可用


def test_run_context_has_unique_task_ids():
    actor = actor_for("qq", OWNER, "Zero", owner_id=OWNER, allowed_senders={OWNER})
    ctx1 = RunContext(session=_session(), actor=actor)
    ctx2 = RunContext(session=_session(), actor=actor)
    assert ctx1.task_id != ctx2.task_id

import json
from pathlib import Path
from types import SimpleNamespace

from core.guards.cooldown import CooldownGuard
from core.schemas import PreferenceConfig

ROOT = Path(__file__).resolve().parents[1]


def build_event(
    group_id: str | None = "group-1",
    *,
    is_admin: bool = False,
    user_id: str = "user-1",
):
    return SimpleNamespace(
        is_admin=lambda: is_admin,
        get_group_id=lambda: group_id,
        get_sender_id=lambda: user_id,
    )


def test_admin_is_exempt_from_existing_group_cooldown() -> None:
    guard = CooldownGuard(PreferenceConfig(group_cooldown=60))
    member = build_event()
    admin = build_event(is_admin=True)

    guard.mark_cooldown(member)

    assert guard.check(member).allowed is False
    assert guard.check(admin).allowed is True


def test_admin_drawing_does_not_start_or_refresh_group_cooldown() -> None:
    guard = CooldownGuard(PreferenceConfig(group_cooldown=60))
    member = build_event()
    admin = build_event(is_admin=True)

    guard.mark_cooldown(admin)

    assert guard.group_cooldowns == {}

    guard.mark_cooldown(member)
    timestamp = guard.group_cooldowns["group-1"]
    guard.mark_cooldown(admin)

    assert guard.group_cooldowns["group-1"] == timestamp


def test_member_cooldown_still_applies() -> None:
    guard = CooldownGuard(PreferenceConfig(group_cooldown=60))
    member = build_event()

    guard.mark_cooldown(member)

    check = guard.check(member)

    assert check.allowed is False
    assert check.remaining is not None
    assert check.remaining > 0


def test_admin_exemption_can_be_disabled() -> None:
    guard = CooldownGuard(
        PreferenceConfig(group_cooldown=60, admin_skip_cooldown=False)
    )
    admin = build_event(is_admin=True)

    guard.mark_cooldown(admin)

    assert guard.group_cooldowns["group-1"] > 0
    assert guard.check(admin).allowed is False


def test_admin_skip_cooldown_default_matches_schema() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    item = schema["preference_config"]["items"]["admin_skip_cooldown"]

    assert item["type"] == "bool"
    assert item["default"] is True
    assert PreferenceConfig().admin_skip_cooldown is True


def test_user_cooldown_blocks_only_the_same_user_in_the_same_group() -> None:
    guard = CooldownGuard(PreferenceConfig(user_cooldown=60))
    member = build_event(user_id="user-1")
    other = build_event(user_id="user-2")

    guard.mark_cooldown(member)

    assert guard.check(member).allowed is False
    assert guard.check(other).allowed is True


def test_user_cooldown_is_scoped_per_group_and_covers_private_chat() -> None:
    guard = CooldownGuard(PreferenceConfig(user_cooldown=60))
    member = build_event(user_id="user-1")
    private = build_event(group_id=None, user_id="user-1")

    guard.mark_cooldown(member)

    assert guard.check(build_event("group-2", user_id="user-1")).allowed is True

    private_guard = CooldownGuard(PreferenceConfig(user_cooldown=60))
    private_guard.mark_cooldown(private)

    assert private_guard.check(private).allowed is False
    assert (
        private_guard.check(build_event(None, user_id="user-2")).allowed is True
    )


def test_admin_is_exempt_from_user_cooldown() -> None:
    guard = CooldownGuard(PreferenceConfig(user_cooldown=60))
    member = build_event(user_id="user-1")
    admin = build_event(is_admin=True, user_id="user-1")

    guard.mark_cooldown(member)
    assert guard.check(admin).allowed is True

    guard.user_cooldowns.clear()
    guard.mark_cooldown(admin)
    assert guard.user_cooldowns == {}
    assert guard.check(member).allowed is True


def test_both_cooldowns_share_the_longest_remaining_time() -> None:
    guard = CooldownGuard(PreferenceConfig(group_cooldown=30, user_cooldown=120))
    member = build_event(user_id="user-1")

    guard.mark_cooldown(member)
    check = guard.check(member)

    assert check.allowed is False
    assert check.remaining is not None and check.remaining > 30
    assert "个人冷却" in check.message


def test_user_cooldown_default_matches_schema() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    item = schema["preference_config"]["items"]["user_cooldown"]

    assert item["type"] == "int"
    assert item["default"] == 0
    assert PreferenceConfig().user_cooldown == 0

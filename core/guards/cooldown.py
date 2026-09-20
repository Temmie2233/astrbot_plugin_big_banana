from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .whitelist import AccessCheck

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..schemas import PreferenceConfig


class CooldownGuard:
    """冷却时间安全守卫，校验群组与个人的最近绘图间隔。"""

    def __init__(self, preference_config: PreferenceConfig) -> None:
        self.preference_config = preference_config
        self.group_cooldowns: dict[str, float] = {}
        # 个人冷却按 (群 ID, 用户 ID) 记录，私聊时群 ID 为 None。
        self.user_cooldowns: dict[tuple[str | None, str], float] = {}

    @property
    def cooldown_seconds(self) -> float:
        return self.preference_config.group_cooldown

    @property
    def user_cooldown_seconds(self) -> float:
        return self.preference_config.user_cooldown

    def _is_admin_exempt(self, event: AstrMessageEvent) -> bool:
        """判断当前事件是否属于豁免冷却的管理员。"""
        return self.preference_config.admin_skip_cooldown and event.is_admin()

    @staticmethod
    def _remaining(started_at: float | None, cooldown_seconds: float) -> int | None:
        """计算某个时间戳的剩余秒数，未记录或已结束时返回 None。"""
        if started_at is None or cooldown_seconds <= 0:
            return None
        elapsed = time.time() - started_at
        if elapsed >= cooldown_seconds:
            return None
        return int(cooldown_seconds - elapsed)

    def group_cooldown_remaining(self, group_id: str | None) -> int | None:
        """计算群组绘图冷却剩余秒数。"""
        if not group_id:
            return None
        return self._remaining(
            self.group_cooldowns.get(group_id), self.cooldown_seconds
        )

    def user_cooldown_remaining(
        self, group_id: str | None, user_id: str | None
    ) -> int | None:
        """计算同一群/会话内个人绘图冷却剩余秒数。"""
        if not user_id:
            return None
        return self._remaining(
            self.user_cooldowns.get((group_id, user_id)), self.user_cooldown_seconds
        )

    def mark_cooldown(self, event: AstrMessageEvent) -> None:
        """记录群组与个人最近一次绘图时间，管理员豁免时不记录。"""
        if self._is_admin_exempt(event):
            return
        now = time.time()
        group_id = event.get_group_id()
        if group_id and self.cooldown_seconds > 0:
            self.group_cooldowns[group_id] = now
        user_id = event.get_sender_id()
        if user_id and self.user_cooldown_seconds > 0:
            self.user_cooldowns[(group_id, user_id)] = now

    def check(self, event: AstrMessageEvent) -> AccessCheck:
        """检查当前群聊与当前用户是否仍处于绘图冷却中。"""
        if self._is_admin_exempt(event):
            return AccessCheck(allowed=True)

        group_id = event.get_group_id()
        group_remaining = self.group_cooldown_remaining(group_id)
        user_remaining = self.user_cooldown_remaining(group_id, event.get_sender_id())
        if group_remaining is None and user_remaining is None:
            return AccessCheck(allowed=True)

        remaining = max(
            value for value in (group_remaining, user_remaining) if value is not None
        )
        if group_remaining is not None and user_remaining is not None:
            message = (
                f"当前群处于画图冷却中（剩余 {group_remaining} 秒），"
                f"你个人冷却剩余 {user_remaining} 秒，请稍后再试。"
            )
        elif group_remaining is not None:
            message = (
                f"当前群处于画图冷却中，冷却时间为 {int(self.cooldown_seconds)} 秒，"
                f"剩余 {group_remaining} 秒，请稍后再试。"
            )
        else:
            message = (
                f"你处于画图冷却中，冷却时间为 {int(self.user_cooldown_seconds)} 秒，"
                f"剩余 {user_remaining} 秒，请稍后再试。"
            )

        return AccessCheck(
            allowed=False,
            message=message,
            log_message=(
                f"[BIG BANANA] 冷却中：群 {group_id} 剩余 {group_remaining} 秒，"
                f"用户 {event.get_sender_id()} 剩余 {user_remaining} 秒"
            ),
            remaining=remaining,
        )

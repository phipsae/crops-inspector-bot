from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config
import database


@dataclass
class RateLimitDecision:
    allowed: bool
    reason: str = ""
    retry_after_seconds: int = 0

    @classmethod
    def ok(cls) -> "RateLimitDecision":
        return cls(allowed=True)

    @classmethod
    def denied(cls, reason: str, retry_after_seconds: int) -> "RateLimitDecision":
        return cls(allowed=False, reason=reason, retry_after_seconds=retry_after_seconds)


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    next_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((next_midnight - now).total_seconds())


def format_countdown(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m" if m else f"{h}h"


async def check(telegram_id: int) -> RateLimitDecision:
    # 1. Owner exemption
    if telegram_id in config.OWNER_TELEGRAM_IDS:
        return RateLimitDecision.ok()

    # 2. Per-user cooldown
    last_at = await database.last_query_at_for_user(telegram_id)
    if last_at is not None:
        elapsed = (datetime.now(timezone.utc) - last_at).total_seconds()
        if elapsed < config.RATE_LIMIT_USER_COOLDOWN_SECONDS:
            wait = int(config.RATE_LIMIT_USER_COOLDOWN_SECONDS - elapsed) + 1
            return RateLimitDecision.denied(
                f"Slow down — please wait {wait}s before your next request.",
                wait,
            )

    # 3. Per-user daily cap
    user_today = await database.count_user_queries_today(telegram_id)
    if user_today >= config.RATE_LIMIT_PER_USER_PER_DAY:
        wait = _seconds_until_utc_midnight()
        return RateLimitDecision.denied(
            f"You've used {user_today}/{config.RATE_LIMIT_PER_USER_PER_DAY} "
            f"requests today. Try again in {format_countdown(wait)}.",
            wait,
        )

    # 4. Global daily cap
    global_today = await database.count_global_queries_today()
    if global_today >= config.RATE_LIMIT_GLOBAL_PER_DAY:
        wait = _seconds_until_utc_midnight()
        return RateLimitDecision.denied(
            f"The bot has hit its daily request limit. "
            f"Try again in {format_countdown(wait)}.",
            wait,
        )

    return RateLimitDecision.ok()

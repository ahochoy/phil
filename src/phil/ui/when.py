"""When something happened, in words: `5 minutes ago`, `yesterday`, `12 days ago`, `on 17 Sep`."""

from datetime import datetime

MONTH_DAYS = 30  # older than this, the date itself says more than a count of days


def _count(n: int, unit: str) -> str:
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def ago(then: datetime, now: datetime) -> str:
    """`then` relative to `now` (both aware). A `then` after `now` is `just now`."""
    seconds = max((now - then).total_seconds(), 0)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return _count(int(seconds // 60), "minute")
    if seconds < 86400:
        return _count(int(seconds // 3600), "hour")
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < MONTH_DAYS:
        return _count(days, "day")
    local = then.astimezone(now.tzinfo)
    year = "" if local.year == now.year else f" {local.year}"
    return f"on {local.day} {local:%b}{year}"

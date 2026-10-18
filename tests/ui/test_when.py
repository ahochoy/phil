from datetime import UTC, datetime, timedelta

import pytest

from phil.ui.when import ago

NOW = datetime(2026, 10, 17, 23, 39, tzinfo=UTC)


@pytest.mark.parametrize(("before", "expected"), [
    (timedelta(seconds=0), "just now"),
    (timedelta(seconds=59), "just now"),
    (timedelta(minutes=1), "1 minute ago"),
    (timedelta(minutes=59, seconds=59), "59 minutes ago"),
    (timedelta(hours=1), "1 hour ago"),
    (timedelta(hours=23, minutes=59), "23 hours ago"),
    (timedelta(hours=24), "yesterday"),
    (timedelta(hours=47), "yesterday"),
    (timedelta(days=2), "2 days ago"),
    (timedelta(days=12, hours=8), "12 days ago"),
    (timedelta(days=29), "29 days ago"),
    (timedelta(days=30), "on 17 Sep"),
    (timedelta(days=400), "on 12 Sep 2025"),
])
def test_ago_says_how_long_before_now(before, expected):
    assert ago(NOW - before, NOW) == expected


def test_a_time_after_now_is_just_now():
    """A clock that moved back (or a file touched by another machine) never reads as the future."""
    assert ago(NOW + timedelta(minutes=5), NOW) == "just now"

from __future__ import annotations

from datetime import date

from calendar_agent.recurrence import rrule


def test_builds_weekly_byday_with_until():
    assert rrule(["SA"], date(2026, 11, 1), until=date(2026, 11, 30)) == "FREQ=WEEKLY;BYDAY=SA;UNTIL=20261130"


def test_count_is_preferred_over_until():
    assert rrule(["MO", "WE"], date(2026, 11, 1), until=date(2026, 12, 31), count=4) == "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4"


def test_empty_or_unknown_weekdays_produce_no_rule():
    assert rrule([], date(2026, 11, 1)) is None
    assert rrule(["XX"], date(2026, 11, 1)) is None


def test_until_defaults_to_end_of_month():
    assert rrule(["SA"], date(2026, 2, 10)) == "FREQ=WEEKLY;BYDAY=SA;UNTIL=20260228"

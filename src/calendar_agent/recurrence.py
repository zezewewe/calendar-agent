"""Turn the model's weekday rule into an RFC 5545 RRULE.

The model supplies the *rule* ("weekly on Saturday until 2026-11-30"); this builds
the RRULE string that WebCalendar's ``add_recurring_event`` parses. WebCalendar owns
the recurrence engine, so the series stays one recurring event (with proper
exceptions in its UI and .ics export) instead of a pile of independent events.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date

# ISO-ish weekday codes the model is asked to emit (RFC 5545 BYDAY values).
WEEKDAY_CODES = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}


def _end_of_month(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def rrule(weekdays: list[str], start: date, until: date | None = None, count: int | None = None) -> str | None:
    """An RRULE for the given weekdays, or None when there are none.

    Bounded by ``count`` when given, else by ``until`` (calendar date, inclusive),
    which defaults to the end of ``start``'s month so "every Saturday in November"
    is safe even if the model omits the end.
    """
    days = [w for w in (weekdays or []) if w in WEEKDAY_CODES]
    if not days:
        return None
    parts = ["FREQ=WEEKLY", "BYDAY=" + ",".join(days)]
    if count:
        parts.append(f"COUNT={int(count)}")
    else:
        end = until or _end_of_month(start)
        parts.append(f"UNTIL={end.strftime('%Y%m%d')}")
    return ";".join(parts)

"""Deterministic expansion of a recurrence rule into concrete dates.

The model only supplies the *rule* ("weekly on Saturday until 2026-11-30"); this
module computes the dates. Models are unreliable at date arithmetic and happily
label a Sunday as Saturday, so - like the timezone handling in ``extract.py`` -
the weekday logic lives here, in one tested place.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

# ISO-ish weekday codes the model is asked to emit.
WEEKDAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

# Hard ceiling so a "daily" rule with no end can never explode the proposal.
MAX_OCCURRENCES = 366


def _end_of_month(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def occurrences(
    weekdays: list[str],
    start: date,
    until: date | None = None,
    count: int | None = None,
    limit: int = MAX_OCCURRENCES,
) -> tuple[list[date], bool]:
    """Dates matching ``weekdays`` from ``start`` (inclusive).

    Stops after ``count`` occurrences, or on/after ``until`` (inclusive). When
    neither is given the series is bounded to the end of ``start``'s month, so
    "every Saturday in November" is safe even if the model omits ``until``.
    Returns ``(dates, capped)`` where ``capped`` means ``limit`` was hit.
    """
    days = {WEEKDAY_CODES[w] for w in weekdays if w in WEEKDAY_CODES}
    if not days:
        return [], False
    if until is None and count is None:
        until = _end_of_month(start)
    if until is not None and start > until:
        return [], False

    found: list[date] = []
    capped = False
    d = start
    while True:
        if d.weekday() in days:
            found.append(d)
            if count is not None and len(found) >= count:
                break
            if len(found) >= limit:
                capped = True
                break
        d += timedelta(days=1)
        if until is not None and d > until:
            break
        if (d - start).days > 366 * 5:  # runaway guard when count is huge
            capped = True
            break
    return found, capped

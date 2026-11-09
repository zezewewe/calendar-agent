from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import ExistingEvent, ProposedEvent


class Calendar(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[ExistingEvent]: ...

    def create_event(self, event: ProposedEvent, end: datetime, user_id: str | None = None) -> str:
        """Create the event, return its id. `end` is resolved; `user_id` is the confirmed caller."""
        ...

    def delete_event(self, event_id: str) -> None: ...


class SharedCalendarFactory:
    """Give every caller the same calendar while Agent retains their identity for tagging."""

    def __init__(self, calendar: Calendar):
        self._calendar = calendar

    def for_user(self, user_id: str) -> Calendar:
        return self._calendar

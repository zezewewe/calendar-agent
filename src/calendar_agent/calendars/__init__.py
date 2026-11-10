from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import ExistingEvent, ProposedEvent


class Calendar(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[ExistingEvent]: ...

    def find_conflicts(self, start: datetime, end: datetime) -> list[ExistingEvent]:
        """Existing events overlapping the slot, per the backend's own conflict check."""
        ...

    def busy_blocks(self, start: datetime, end: datetime) -> list[ExistingEvent]:
        """Busy time blocks in the range, without reading event details."""
        ...

    def create_event(self, event: ProposedEvent, end: datetime, user_id: str | None = None) -> str:
        """Create the event, return its id. `end` is resolved; `user_id` is the confirmed caller."""
        ...

    def update_event(self, event_id: str, event: ProposedEvent, end: datetime, user_id: str | None = None) -> str:
        """Change an existing event in place where the backend supports it; return its id.

        Backends without an update primitive may fall back to delete + create.
        """
        ...

    def delete_event(self, event_id: str) -> None: ...


class SharedCalendarFactory:
    """Give every caller the same calendar while Agent retains their identity for tagging."""

    def __init__(self, calendar: Calendar):
        self._calendar = calendar

    def for_user(self, user_id: str) -> Calendar:
        return self._calendar

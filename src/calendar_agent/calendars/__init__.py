from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import ExistingEvent, ProposedEvent


class Calendar(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[ExistingEvent]: ...

    def create_event(self, event: ProposedEvent, end: datetime) -> str:
        """Create the event, return its id. `end` is already resolved (never None)."""
        ...

    def delete_event(self, event_id: str) -> None: ...

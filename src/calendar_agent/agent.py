"""Channel-agnostic core: Inbound -> Proposal (no side effects) -> confirm() writes to the calendar."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from .calendars import Calendar
from .extract import Extractor
from .models import ExistingEvent, Inbound, Proposal, ProposedEvent
from .store import Store


def resolve_end(e: ProposedEvent, default_minutes: int) -> datetime:
    if e.end:
        return e.end
    if e.all_day:
        return datetime.combine(e.start.date() + timedelta(days=1), datetime.min.time(), tzinfo=e.start.tzinfo)
    return e.start + timedelta(minutes=default_minutes)


def overlaps(a_start: datetime, a_end: datetime, b: ExistingEvent) -> bool:
    return a_start < b.end and b.start < a_end


class Agent:
    def __init__(self, extractor: Extractor, calendar: Calendar, store: Store, default_duration_min: int = 60):
        self._x, self._cal, self._store, self._dur = extractor, calendar, store, default_duration_min

    def propose(self, inbound: Inbound) -> Proposal:
        ext = self._x.extract(inbound)
        events = sorted(ext.events, key=lambda e: e.start)
        conflicts: dict[int, list[ExistingEvent]] = {}
        if events:
            ends = [resolve_end(e, self._dur) for e in events]
            existing = self._cal.list_events(min(e.start for e in events), max(ends))
            for i, (e, end) in enumerate(zip(events, ends)):
                hits = [x for x in existing if overlaps(e.start, end, x)]
                if hits:
                    conflicts[i] = hits
        p = Proposal(id=uuid.uuid4().hex[:12], user_id=inbound.user_id, events=events, conflicts=conflicts, questions=ext.questions)
        if events:
            self._store.save(p)
        return p

    def confirm(self, proposal_id: str, user_id: str) -> list[str] | None:
        """Create the events. Returns created ids, or None if the proposal is gone/not yours."""
        p = self._store.get(proposal_id)
        if p is None or p.user_id != user_id:
            return None
        p = self._store.take(proposal_id)  # atomic claim; None if a concurrent confirm won
        if p is None:
            return None
        ids: list[str] = []
        try:
            for e in p.events:
                ids.append(self._cal.create_event(e, resolve_end(e, self._dur)))
        finally:
            if ids:
                self._store.record_created(p.id, ids)
        return ids

    def cancel(self, proposal_id: str, user_id: str) -> bool:
        p = self._store.get(proposal_id)
        return bool(p and p.user_id == user_id and self._store.take(proposal_id))

    def undo(self, proposal_id: str) -> int:
        ids = self._store.pop_created(proposal_id)
        for i in ids:
            self._cal.delete_event(i)
        return len(ids)

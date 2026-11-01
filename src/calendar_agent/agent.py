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

    def _build(self, proposal_id: str, user_id: str, ext) -> Proposal:
        events = sorted(ext.events, key=lambda e: e.start)
        conflicts: dict[int, list[ExistingEvent]] = {}
        if events:
            ends = [resolve_end(e, self._dur) for e in events]
            existing = self._cal.list_events(min(e.start for e in events), max(ends))
            for i, (e, end) in enumerate(zip(events, ends)):
                hits = [x for x in existing if overlaps(e.start, end, x)]
                if hits:
                    conflicts[i] = hits
        p = Proposal(id=proposal_id, user_id=user_id, events=events, conflicts=conflicts, questions=ext.questions)
        if events:
            self._store.save(p)
        return p

    def propose(self, inbound: Inbound) -> Proposal:
        return self._build(uuid.uuid4().hex[:12], inbound.user_id, self._x.extract(inbound))

    def revise(self, proposal_id: str, user_id: str, instruction: str, now: datetime) -> Proposal | None:
        """Edit a still-pending proposal. Returns the new proposal, or None if gone/not yours.

        The old proposal is claimed (removed) only after the revision succeeded, so a failed
        LLM call leaves the original confirmable.
        """
        old = self._store.get(proposal_id)
        if old is None or old.user_id != user_id:
            return None
        ext = self._x.revise(old, instruction, now)
        if self._store.take(proposal_id) is None:  # confirmed/cancelled while we were thinking
            return None
        return self._build(uuid.uuid4().hex[:12], user_id, ext)

    def link_message(self, chat_id: str, message_id: str, proposal_id: str) -> None:
        self._store.link_message(chat_id, message_id, proposal_id)

    def proposal_for_message(self, chat_id: str, message_id: str) -> str | None:
        return self._store.proposal_for_message(chat_id, message_id)

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
                self._store.record_created(p.id, user_id, ids)
        return ids

    def cancel(self, proposal_id: str, user_id: str) -> bool:
        p = self._store.get(proposal_id)
        return bool(p and p.user_id == user_id and self._store.take(proposal_id))

    def undo(self, proposal_id: str, user_id: str | None = None) -> int:
        """Remove the events this proposal created. Pass `user_id` to scope it to one user."""
        ids = self._store.pop_created(proposal_id, user_id)
        for i in ids:
            self._cal.delete_event(i)
        return len(ids)
